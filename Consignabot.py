from pathlib import Path
import discord
from discord.ext import tasks
import datetime
import re
import asyncio
from event_series import EventSeries, RepetitionType
from typing import Optional

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)

directory = Path("data")
directory.mkdir(exist_ok=True)


def _parse_iso_datetime(text: str) -> Optional[datetime.datetime]:
    # Parse an ISO datetime string, treating date-only inputs as 9:00 AM to avoid midnight confusion
    if text is None:
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            dt = datetime.datetime.fromisoformat(text)
            return dt.replace(hour=9, minute=0, second=0, microsecond=0)
        dt = datetime.datetime.fromisoformat(text)
        if dt.hour == 0 and dt.minute == 0 and dt.second == 0 and not any(ch in text for ch in ("T", " ", ":")):
            return dt.replace(hour=9, minute=0, second=0, microsecond=0)
        return dt
    except Exception:
        return None


async def _safe_send_with_reactions(channel: discord.abc.Messageable, content: str, reactions: list[str]) -> Optional[discord.Message]:
    try:
        sent = await channel.send(content)
    except Exception:
        return None
    for r in reactions:
        try:
            await sent.add_reaction(r)
        except Exception:
            continue
    return sent


def _add_responsible(existing: str, user_id: int) -> str:
    mention = f"<@{user_id}>"
    if not existing or not existing.strip():
        return mention
    parts = [p.strip() for p in existing.split(",") if p.strip()]
    if mention in parts:
        return existing
    parts.append(mention)
    return ", ".join(parts)


@client.event
async def on_ready():
    print(f"Connecté en tant que {client.user}")
    if not check_events.is_running():
        check_events.start()


@client.event
async def on_message(message: discord.Message):
    if message.author == client.user:
        return

    if message.content.startswith("$bac_plein"):
        parts = message.content.split(" ", 1)
        if len(parts) > 1:
            try:
                await message.delete()
            except Exception:
                pass

            number_text = parts[1].strip()
            now = datetime.datetime.now()
            series_name = f"Bac_{number_text}_plein_{now.replace(microsecond=0).isoformat()}"
            try:
                series = EventSeries(
                    repetition=RepetitionType.NONE,
                    club="@everyone",
                    responsible="",
                    name=series_name,
                    channel=message.channel.id,
                    next_event=now,
                )
            except Exception as ex:
                await message.channel.send(f"Erreur lors de la création de la série : {ex}")
                return

            try:
                series.save_to_file(channel_identifier=message.channel.id, directory=str(directory), overwrite=False)
            except FileExistsError:
                await message.channel.send(f'Une série d\'événements avec le nom "{series.name}" existe déjà dans ce canal.')
                return
            except Exception as ex:
                await message.channel.send(f"Échec de l'enregistrement de la série : {ex}")
                return
        return

    if not message.content.startswith("$consignabot"):
        return

    parts = message.content.split(" ")

    if len(parts) == 1 or parts[1] == "help":
        await message.channel.send(
            "Utilisation : $consignabot [commande]\n"
            "Commandes :\n"
            "- create [repetition] [club] [name] [next_event]: Crée une série\n"
            "next_event peut être au format date (YYYY-MM-DD) ou datetime (YYYY-MM-DDTHH:MM), les dates sans heure seront traitées comme 9:00 du matin.\n"
            "repetition doit être l'un de NONE, DAILY, WEEKLY, BIWEEKLY, MONTHLY, YEARLY\n"
            "- list: Liste les séries pour ce canal\n"
            "- info [name]: Affiche les informations d'une série\n"
            "- delete [name]: Supprime une série\n"
            "Le code source est disponible à https://github.com/sonia-auv/Consignabot (voir avec SONIA pour les accès)"
        )
        return

    cmd = parts[1].lower()

    if cmd == "create":
        if len(parts) < 6:
            await message.channel.send("Usage : $consignabot create [repetition] [club] [name] [next_event]")
            return

        repetition_token = parts[2]
        club_token = parts[3]
        name_token = parts[4]
        next_event_raw = " ".join(parts[5:]).strip()
        next_event_dt = _parse_iso_datetime(next_event_raw)
        if next_event_dt is None:
            await message.channel.send("Date invalide ou manquante. Fournissez next_event au format ISO (YYYY-MM-DD ou YYYY-MM-DDTHH:MM).")
            return

        try:
            series = EventSeries(
                repetition=RepetitionType(repetition_token),
                club=club_token,
                responsible="",
                name=name_token,
                channel=message.channel.id,
                next_event=next_event_dt,
            )
        except Exception as ex:
            await message.channel.send(f"Erreur lors de la création de la série : {ex}")
            return

        try:
            series.save_to_file(channel_identifier=message.channel.id, directory=str(directory), overwrite=False)
        except FileExistsError:
            prompt_text = (f'Une série d\'événements avec le nom "{series.name}" existe déjà dans ce canal.\n'
                           "Réagissez avec ✅ pour remplacer, ❌ pour annuler (30s).")
            try:
                prompt = await message.channel.send(prompt_text)
                await prompt.add_reaction("✅")
                await prompt.add_reaction("❌")
            except Exception:
                await message.channel.send(f'Une série d\'événements avec le nom "{series.name}" existe déjà dans ce canal.')
                return

            def check(reaction: discord.Reaction, user: discord.User):
                return user == message.author and reaction.message.id == prompt.id and str(reaction.emoji) in ("✅", "❌")

            try:
                reaction, user = await client.wait_for("reaction_add", timeout=30.0, check=check)
            except asyncio.TimeoutError:
                try:
                    await prompt.edit(content="Annulé : pas de réaction dans le temps imparti.")
                except Exception:
                    pass
                return

            if str(reaction.emoji) == "✅":
                try:
                    series.save_to_file(channel_identifier=message.channel.id, directory=str(directory), overwrite=True)
                    try:
                        await prompt.edit(content=f'Série "{series.name}" remplacée.')
                    except Exception:
                        pass
                except Exception as ex:
                    try:
                        await prompt.edit(content=f'Échec du remplacement : {ex}')
                    except Exception:
                        pass
                    return
            else:
                try:
                    await prompt.edit(content=f'Remplacement annulé pour la série "{series.name}".')
                except Exception:
                    pass
            return

        except Exception as ex:
            await message.channel.send(f"Échec de l'enregistrement de la série : {ex}")
            return

        next_event_display = series.next_event.replace(microsecond=0).isoformat(sep=" ")
        embed = discord.Embed(
            title="Série d'événements créée",
            description=f"Pour {message.channel.mention}",
            color=discord.Color.green(),
            timestamp=datetime.datetime.now(),
        )
        embed.add_field(name="Nom", value=series.name or "—", inline=False)
        embed.add_field(name="Club", value=series.club or "—", inline=True)
        embed.add_field(name="Répétition", value=getattr(series.repetition, "value", str(series.repetition)), inline=True)
        embed.add_field(name="Responsable", value=series.responsible or "—", inline=False)
        embed.add_field(name="Prochain évènement", value=next_event_display, inline=False)
        embed.set_footer(text="Consignabot")
        await message.channel.send(embed=embed)
        return

    if cmd == "list":
        channel_prefix = f"{message.channel.id}."
        files = [p for p in directory.iterdir() if p.is_file() and p.name.endswith(".json") and p.name.startswith(channel_prefix)]
        if not files:
            await message.channel.send("Aucune série d'événements trouvée pour ce canal.")
            return

        embed = discord.Embed(
            title="Séries",
            description=f"Pour {message.channel.mention}",
            color=discord.Color.blue(),
            timestamp=datetime.datetime.now(),
        )

        for fp in files:
            try:
                series = EventSeries.load_from_file(str(fp))
            except Exception as ex:
                err = discord.Embed(title="Impossible de lire la série", description=f"Fichier : {fp.name}", color=discord.Color.red())
                err.add_field(name="Erreur", value=str(ex), inline=False)
                await message.channel.send(embed=err)
                continue

            ne = series.next_event.replace(microsecond=0).isoformat(sep=" ")
            embed.add_field(name="Nom", value=series.name or "—", inline=True)
            embed.add_field(name="Club", value=series.club or "—", inline=True)
            embed.add_field(name="Répétition", value=str(series.repetition.value), inline=True)
            embed.add_field(name="Prochain évènement", value=ne, inline=True)
            embed.add_field(name="\u200B", value="\u200B", inline=False)

        embed.set_footer(text="Consignabot")
        await message.channel.send(embed=embed)
        return

    if cmd == "delete":
        if len(parts) != 3:
            await message.channel.send("Usage : $consignabot delete [name]")
            return
        name_to_delete = parts[2]
        path = directory / f"{message.channel.id}.{name_to_delete}.json"
        if not path.exists():
            await message.channel.send(f'Aucune série trouvée avec le nom "{name_to_delete}".')
            return
        path.unlink()
        await message.channel.send(f'Série "{name_to_delete}" supprimée.')
        return

    if cmd == "info":
        if len(parts) != 3:
            await message.channel.send("Usage : $consignabot info [name]")
            return
        name_to_info = parts[2]
        path = directory / f"{message.channel.id}.{name_to_info}.json"
        if not path.exists():
            await message.channel.send(f'Aucune série trouvée avec le nom "{name_to_info}".')
            return
        try:
            series = EventSeries.load_from_file(str(path))
        except Exception as ex:
            await message.channel.send(f"Erreur lors de la lecture de la série : {ex}")
            return

        ne = series.next_event.replace(microsecond=0).isoformat(sep=" ")
        embed = discord.Embed(
            title=f"Série d'événements : {series.name}",
            description=f"Pour {message.channel.mention}",
            color=discord.Color.purple(),
            timestamp=datetime.datetime.now(),
        )
        embed.add_field(name="Nom", value=series.name or "—", inline=True)
        embed.add_field(name="Club", value=series.club or "—", inline=True)
        embed.add_field(name="Répétition", value=str(series.repetition.value), inline=True)
        embed.add_field(name="Responsable", value=series.responsible or "—", inline=True)
        embed.add_field(name="Prochain évènement", value=ne, inline=True)
        embed.set_footer(text="Consignabot")
        await message.channel.send(embed=embed)
        return


@client.event
async def on_reaction_add(reaction: discord.Reaction, user: discord.abc.User):
    if user == client.user:
        return

    msg = reaction.message
    emoji = str(reaction.emoji)
    if emoji not in ("✋", "✅"):
        return

    # Find matching EventSeries by last_message_id
    for fp in directory.iterdir():
        if not fp.is_file() or not fp.name.endswith(".json"):
            continue
        try:
            series = EventSeries.load_from_file(str(fp))
        except Exception:
            continue

        if series.last_message_id != msg.id:
            continue

        if emoji == "✋":
            new_responsible = _add_responsible(series.responsible, user.id)
            try:
                await msg.channel.send(f"{user.mention} est maintenant responsable pour la série \"{series.name}\".")
            except Exception:
                pass

            new_series = EventSeries(
                repetition=series.repetition,
                club=series.club,
                responsible=new_responsible,
                name=series.name,
                channel=series.channel,
                next_event=series.next_event,
                next_message=series.next_message,
                last_message_id=series.last_message_id,
            )

        else:  # emoji == "✅"
            try:
                await msg.channel.send(f"{user.mention} a marqué la série \"{series.name}\" comme terminée.")
            except Exception:
                pass

            # If repetition is NONE -> delete the file
            if series.repetition == RepetitionType.NONE:
                try:
                    path = Path(series.get_filepath(series.channel, str(directory)))
                    if path.exists():
                        path.unlink()
                    try:
                        await msg.channel.send(f'La série "{series.name}" (non récurrente) a été supprimée.')
                    except Exception:
                        pass
                except Exception:
                    try:
                        await msg.channel.send(f'Échec de la suppression de la série non récurrente "{series.name}".')
                    except Exception:
                        pass
                break

            # advance dates only when marking DONE
            new_series = series.clear_responsibles().with_advanced_next_event()

        # Save updated series
        try:
            new_series.save_to_file(channel_identifier=new_series.channel, directory=str(directory), overwrite=True)
        except Exception:
            try:
                await msg.channel.send(f'Échec de la mise à jour de la série "{series.name}" après la réaction.')
            except Exception:
                pass

        break


@tasks.loop(minutes=1.0)
async def check_events():
    await client.wait_until_ready()
    now = datetime.datetime.now()

    for fp in directory.glob("*.json"):
        if not fp.is_file():
            continue
        try:
            series = EventSeries.load_from_file(str(fp))
        except Exception:
            continue

        # next_event is required by model — treat it as the canonical schedule
        next_message = series.next_message

        # Normalize next_message to datetime (it will be present because model defaults it)
        next_message_dt = next_message if isinstance(next_message, datetime.datetime) else datetime.datetime.fromisoformat(str(next_message))

        # If next_event exists and is in the future, keep next_message aligned and skip sending
        if series.next_event and series.next_event > now:
            if next_message_dt != series.next_event:
                updated = series.sync_next_message_to_event()
                try:
                    updated.save_to_file(channel_identifier=series.channel, directory=str(directory), overwrite=True)
                except Exception:
                    pass
            continue

        # If next_message not yet due, skip
        if now < next_message_dt:
            continue

        try:
            name = series.name
            club = series.club
            responsible = series.responsible

            # compute ~20% of repetition as a timedelta
            if series.repetition == RepetitionType.DAILY:
                delta = datetime.timedelta(hours=1)
            elif series.repetition == RepetitionType.WEEKLY:
                delta = datetime.timedelta(days=1)
            elif series.repetition == RepetitionType.BIWEEKLY:
                delta = datetime.timedelta(days=2)
            elif series.repetition == RepetitionType.MONTHLY:
                delta = datetime.timedelta(days=5)
            elif series.repetition == RepetitionType.YEARLY:
                delta = datetime.timedelta(days=30)
            else:
                delta = datetime.timedelta(days=1)

            new_next_message = next_message_dt + delta

            # Determine "state" based on next_event and responsible:
            # - If next_event is past (or equal) and responsible empty => TODO
            # - If next_event is past and responsible non-empty => ASSIGNED
            # - If next_event is in the future => (handled above)
            if series.next_event <= now:
                if not responsible or not responsible.strip():
                    send_text = (
                        f'🔔 La série "{name}" est à faire {club}\n'
                        f'Réagissez avec ✋ pour être assigné, ou ✅ une fois terminée.\n'
                        f'Prochain rappel prévu le {new_next_message.replace(microsecond=0).isoformat(sep=" ")}.'
                    )
                    reactions = ["✋", "✅"]
                else:
                    send_text = (
                        f'⏰ Rappel : la série "{name}" est toujours à faire — responsable(s) : {responsible}.\n'
                        f'Réagissez avec ✋ pour être ajouté aux responsables, ou ✅ une fois terminée.\n'
                        f'Prochain rappel prévu le {new_next_message.replace(microsecond=0).isoformat(sep=" ")}.'
                    )
                    reactions = ["✋", "✅"]
            else:
                # should not reach here because future next_event handled above, but keep safe
                updated = series.sync_next_message_to_event()
                try:
                    updated.save_to_file(channel_identifier=series.channel, directory=str(directory), overwrite=True)
                except Exception:
                    pass
                continue

            channel = client.get_channel(series.channel)
            sent_msg = None
            if channel:
                sent_msg = await _safe_send_with_reactions(channel, send_text, reactions)

            # persist updated series (next_message updated, last_message_id updated)
            updated = EventSeries(
                repetition=series.repetition,
                club=series.club,
                responsible=series.responsible,
                name=series.name,
                channel=series.channel,
                next_event=series.next_event,
                next_message=new_next_message,
                last_message_id=(sent_msg.id if sent_msg is not None else series.last_message_id),
            )
            try:
                updated.save_to_file(channel_identifier=series.channel, directory=str(directory), overwrite=True)
            except Exception:
                pass

        except Exception:
            continue


import os

# Read token from environment variable or file
token = os.getenv("DISCORD_TOKEN")
if not token:
    try:
        with open("token.txt", "r") as f:
            token = f.read().strip()
    except FileNotFoundError:
        token = None

if not token or not token.strip():
    print("Avertissement : token Discord introuvable. Le bot ne démarrera pas sans token.")
else:
    client.run(token)
    print("Le bot s'est arrêté.")
