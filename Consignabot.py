from pathlib import Path
import os
import interactions
import datetime
import re
import asyncio
from event_series import EventSeries, RepetitionType
from typing import Optional
import logging

logging.basicConfig(level=logging.DEBUG)

intents = interactions.Intents.DEFAULT | interactions.Intents.MESSAGE_CONTENT
client = interactions.Client(intents=intents)

directory = Path("data")
directory.mkdir(exist_ok=True)


def _format_datetime(dt: datetime.datetime) -> str:
    return dt.replace(microsecond=0).isoformat(sep=" ")


def _build_series_embed(series: EventSeries, title: str, description: str, color: interactions.BrandColors) -> interactions.Embed:
    embed = interactions.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.datetime.now(),
    )
    embed.add_field(name="Nom", value=series.name or "—", inline=False)
    embed.add_field(name="Club", value=series.club or "—", inline=True)
    embed.add_field(name="Répétition", value=getattr(series.repetition, "value", str(series.repetition)), inline=True)
    embed.add_field(name="Responsable", value=series.responsible or "—", inline=False)
    embed.add_field(name="Prochain évènement", value=_format_datetime(series.next_event), inline=False)
    embed.set_footer(text="Consignabot")
    return embed


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


async def _safe_send_with_reactions(channel_id: int, content: str, reactions: list[str]):
    try:
        channel = await client.fetch_channel(channel_id)
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


@interactions.listen()
async def on_ready():
    async def monitor():
        while True:
            logging.debug("Latency: %s", client.latency)
            await asyncio.sleep(5)

    check_events.start()
    keep_alive.start()
    monitoring_task = asyncio.create_task(monitor())
    logging.info(f"Connecté en tant que {client.user}")


@interactions.slash_command(name="help", description="Affiche l'aide de Consignabot")
async def help_command(interaction: interactions.SlashContext):
    await interaction.defer()
    await interaction.send(
        "Utilisation : /[commande]\n"
        "Commandes :\n"
        "- create [repetition] [club] [name] [next_event]: Crée une série\n"
        "next_event peut être au format date (YYYY-MM-DD) ou datetime (YYYY-MM-DDTHH:MM), les dates sans heure seront traitées comme 9:00 du matin.\n"
        "repetition doit être l'un de NONE, DAILY, WEEKLY, BIWEEKLY, MONTHLY, YEARLY\n"
        "- list: Liste les séries pour ce canal\n"
        "- info [name]: Affiche les informations d'une série\n"
        "- delete [name]: Supprime une série\n"
        "- bac_plein [number]: Crée une série Bac plein\n"
        "Le code source est disponible à https://github.com/consignactionets/Consignabot"
    )


@interactions.slash_command(name="create", description="Crée une série")
@interactions.slash_option(name="repetition", description="Répétition de la série", opt_type=interactions.OptionType.STRING, required=True, choices=[interactions.SlashCommandChoice(name=r.value, value=r.value) for r in RepetitionType])
@interactions.slash_option(name="club", description="Nom du club", opt_type=interactions.OptionType.STRING, required=True)
@interactions.slash_option(name="name", description="Nom de la série", opt_type=interactions.OptionType.STRING, required=True)
@interactions.slash_option(name="next_event", description="YYYY-MM-DD ou YYYY-MM-DDTHH:MM", opt_type=interactions.OptionType.STRING, required=True)
async def create_command(
    interaction: interactions.SlashContext,
    repetition: str,
    club: str,
    name: str,
    next_event: str,
):
    await interaction.defer()
    next_event_dt = _parse_iso_datetime(next_event)
    if next_event_dt is None:
        await interaction.send("Date invalide ou manquante. Fournissez next_event au format ISO (YYYY-MM-DD ou YYYY-MM-DDTHH:MM).")
        return

    try:
        series = EventSeries(
            repetition=RepetitionType(repetition.lower()),
            club=club,
            responsible="",
            name=name,
            channel=interaction.channel.id,
            next_event=next_event_dt,
        )
    except Exception as ex:
        await interaction.send(f"Erreur lors de la création de la série : {ex}")
        return

    try:
        series.save_to_file(channel_identifier=interaction.channel.id, directory=str(directory), overwrite=False)
    except FileExistsError:
        prompt_text = (f'Une série d\'événements avec le nom "{series.name}" existe déjà dans ce canal.\n'
                       "Réagissez avec ✅ pour remplacer, ❌ pour annuler (30s).")
        try:
            prompt = await interaction.send(prompt_text)
            await prompt.add_reaction("✅")
            await prompt.add_reaction("❌")
        except Exception:
            await interaction.send(f'Une série d\'événements avec le nom "{series.name}" existe déjà dans ce canal.')
            return

        def check(reaction, user):
            return user.id == interaction.user.id and reaction.message.id == prompt.id and str(reaction.emoji) in ("✅", "❌")

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
                series.save_to_file(channel_identifier=interaction.channel.id, directory=str(directory), overwrite=True)
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
        await interaction.send(f"Échec de l'enregistrement de la série : {ex}")
        return

    await interaction.send(embed=_build_series_embed(
        series,
        title="Série d'événements créée",
        description=f"Pour {interaction.channel.mention}",
        color=interactions.BrandColors.GREEN,
    ))


@interactions.slash_command(name="list", description="Liste les séries pour ce canal")
async def list_command(interaction: interactions.SlashContext):
    await interaction.defer()
    channel_prefix = f"{interaction.channel.id}."
    files = [p for p in directory.iterdir() if p.is_file() and p.name.endswith(".json") and p.name.startswith(channel_prefix)]
    if not files:
        await interaction.send("Aucune série d'événements trouvée pour ce canal.")
        return

    embed = interactions.Embed(
        title="Séries",
        description=f"Pour {interaction.channel.mention}",
        color=interactions.BrandColors.BLURPLE,
        timestamp=datetime.datetime.now(),
    )

    for fp in files:
        try:
            series = EventSeries.load_from_file(str(fp))
        except Exception as ex:
            err = interactions.Embed(title="Impossible de lire la série", description=f"Fichier : {fp.name}", color=interactions.BrandColors.RED)
            err.add_field(name="Erreur", value=str(ex), inline=False)
            await interaction.send(embed=err)
            return

        ne = series.next_event.replace(microsecond=0).isoformat(sep=" ")
        embed.add_field(name="Nom", value=series.name or "—", inline=True)
        embed.add_field(name="Club", value=series.club or "—", inline=True)
        embed.add_field(name="Répétition", value=str(series.repetition.value), inline=True)
        embed.add_field(name="Prochain évènement", value=ne, inline=True)
        embed.add_field(name="\u200B", value="\u200B", inline=False)

    embed.set_footer(text="Consignabot")
    await interaction.send(embed=embed)


@interactions.slash_command(name="delete", description="Supprime une série")
@interactions.slash_option(name="name_to_delete", description="Nom de la série à supprimer", opt_type=interactions.OptionType.STRING, required=True)
async def delete_command(interaction: interactions.SlashContext, name_to_delete: str):
    await interaction.defer()
    path = directory / f"{interaction.channel.id}.{name_to_delete}.json"
    if not path.exists():
        await interaction.send(f'Aucune série trouvée avec le nom "{name_to_delete}".')
        return
    path.unlink()
    await interaction.send(f'Série "{name_to_delete}" supprimée.')


@interactions.slash_command(name="info", description="Affiche les informations d'une série")
@interactions.slash_option(name="name_to_info", description="Nom de la série", opt_type=interactions.OptionType.STRING, required=True)
async def info_command(interaction: interactions.SlashContext, name_to_info: str):
    await interaction.defer()
    path = directory / f"{interaction.channel.id}.{name_to_info}.json"
    if not path.exists():
        await interaction.send(f'Aucune série trouvée avec le nom "{name_to_info}".')
        return
    try:
        series = EventSeries.load_from_file(str(path))
    except Exception as ex:
        await interaction.send(f"Erreur lors de la lecture de la série : {ex}")
        return

    await interaction.send(embed=_build_series_embed(
        series,
        title=f"Série d'événements : {series.name}",
        description=f"Pour {interaction.channel.mention}",
        color=interactions.BrandColors.FUCHSIA,
    ))


@interactions.slash_command(name="bac_plein", description="Crée une série Bac plein")
@interactions.slash_option(name="number_text", description="Numéro du bac", opt_type=interactions.OptionType.STRING, required=True)
async def bac_plein_command(interaction: interactions.SlashContext, number_text: str):
    await interaction.defer()
    now = datetime.datetime.now()
    series_name = f"Bac_{number_text}_plein_{now.replace(microsecond=0).isoformat()}"
    try:
        series = EventSeries(
            repetition=RepetitionType.NONE,
            club="@everyone",
            responsible="",
            name=series_name,
            channel=interaction.channel.id,
            next_event=now,
        )
    except Exception as ex:
        await interaction.send(f"Erreur lors de la création de la série : {ex}")
        return

    try:
        series.save_to_file(channel_identifier=interaction.channel.id, directory=str(directory), overwrite=False)
    except FileExistsError:
        await interaction.send(f'Une série d\'événements avec le nom "{series.name}" existe déjà dans ce canal.')
        return
    except Exception as ex:
        await interaction.send(f"Échec de l'enregistrement de la série : {ex}")
        return
    await interaction.send(f'Série "{series.name}" créée avec succès.')


@interactions.listen(interactions.events.MessageReactionAdd)
async def on_reaction_add(event: interactions.events.MessageReactionAdd):
    if event.author.id == client.user.id:
        return

    msg = event.message
    emoji = str(event.emoji)
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
            new_responsible = _add_responsible(series.responsible, event.author.id)
            try:
                await msg.channel.send(f"{event.author.mention} est maintenant responsable pour la série \"{series.name}\".")
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
                await msg.channel.send(f"{event.author.mention} a marqué la série \"{series.name}\" comme terminée.")
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


@interactions.Task.create(interactions.IntervalTrigger(minutes=1))
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

            sent_msg = await _safe_send_with_reactions(series.channel, send_text, reactions)

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


@interactions.Task.create(interactions.IntervalTrigger(seconds=15))
async def keep_alive():
    logging.info("Keep-alive check: bot is running.")


# Read token from environment variable or file
token = os.getenv("DISCORD_TOKEN")
if not token:
    try:
        with open("token.txt", "r") as f:
            token = f.read().strip()
    except FileNotFoundError:
        token = None

if not token or not token.strip():
    logging.warning("Avertissement : token Discord introuvable. Le bot ne démarrera pas sans token.")
else:
    client.start(token)
    logging.info("Le bot s'est arrêté.")
