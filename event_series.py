from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict
from datetime import datetime, timedelta
import json
from pathlib import Path
import re
import calendar


class RepetitionType(Enum):
    NONE = "none"
    DAILY = "daily"
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"
    CUSTOM = "custom"


def _safe_filename(s: str) -> str:
    # keep reasonable characters, replace others with underscore
    return re.sub(r'[^\w\-_\. ]', '_', s)


def _add_months(dt: datetime, months: int) -> datetime:
    """Return dt + months months, clamping day to month end when necessary."""
    year = dt.year + (dt.month - 1 + months) // 12
    month = (dt.month - 1 + months) % 12 + 1
    day = dt.day
    last_day = calendar.monthrange(year, month)[1]
    day = min(day, last_day)
    return dt.replace(year=year, month=month, day=day)


def _add_years(dt: datetime, years: int) -> datetime:
    """Return dt + years years, clamping Feb 29 -> Feb 28 when needed."""
    try:
        return dt.replace(year=dt.year + years)
    except ValueError:
        # Feb 29 -> Feb 28 on non-leap years
        return dt.replace(year=dt.year + years, month=2, day=28)


@dataclass(frozen=True)
class EventSeries:
    """
    Immutable value object describing a series of events.

    Notes:
    - `next_event` is required (non-optional). It must be a datetime.
    - `responsible` is a comma-separated list of user mentions (e.g. "<@123>, <@456>").
    """

    repetition: RepetitionType
    club: str
    responsible: str
    name: str
    channel: int
    next_event: datetime
    next_message: datetime | None = None
    last_message_id: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.repetition, RepetitionType):
            raise TypeError("repetition must be a RepetitionType")
        for field_name in ("club", "name"):
            val: Any = getattr(self, field_name)
            if val is None or not isinstance(val, str) or not val.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        if not isinstance(self.channel, int):
            raise TypeError("channel must be an int")
        # next_event is required and must be a datetime
        if not isinstance(self.next_event, datetime):
            raise TypeError("next_event must be a datetime and is required")
        if self.next_message is not None and not isinstance(self.next_message, datetime):
            raise TypeError("next_message must be a datetime or None")
        if self.last_message_id is not None and not isinstance(self.last_message_id, int):
            raise TypeError("last_message_id must be an int or None")

        # Default next_message to next_event if omitted
        if getattr(self, "next_message", None) is None:
            object.__setattr__(self, "next_message", self.next_event)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable dict representation."""
        return {
            "repetition": self.repetition.value,
            "club": self.club,
            "responsible": self.responsible,
            "name": self.name,
            "channel": self.channel,
            "next_event": self.next_event.isoformat(),
            "next_message": self.next_message.isoformat() if self.next_message is not None else None,
            "last_message_id": self.last_message_id,
        }

    def to_json(self, *, indent: int = 2) -> str:
        """Return a JSON string (utf-8 characters preserved)."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def get_filepath(self, channel_identifier: int | str | None = None, directory: str = "data") -> str:
        """
        Build a safe filepath for this series in the given directory.
        Does not create or write the file.
        """
        Path(directory).mkdir(parents=True, exist_ok=True)
        channel_part = channel_identifier if channel_identifier is not None else self.channel
        raw = f"{channel_part}.{self.name}.json"
        filename = _safe_filename(raw)
        return str(Path(directory) / filename)

    def save_to_file(self, channel_identifier: int | str | None = None, directory: str = "data", *, overwrite: bool = False) -> str:
        """
        Save the series to a JSON file and return the filepath.
        If overwrite is False and file exists, raises FileExistsError.
        """
        path = Path(self.get_filepath(channel_identifier, directory))
        if path.exists() and not overwrite:
            raise FileExistsError(path)
        with path.open("w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        return str(path)

    # --- convenience helpers for responsibles --------------------------------

    def add_responsible(self, user_id: int) -> "EventSeries":
        """Return a new EventSeries with the user added to responsibles (id -> mention)."""
        mention = f"<@{user_id}>"
        parts = [p.strip() for p in self.responsible.split(",") if p.strip()] if self.responsible else []
        if mention in parts:
            return self
        parts.append(mention)
        new_resp = ", ".join(parts)
        return EventSeries(
            repetition=self.repetition,
            club=self.club,
            responsible=new_resp,
            name=self.name,
            channel=self.channel,
            next_event=self.next_event,
            next_message=self.next_message,
            last_message_id=self.last_message_id,
        )

    def clear_responsibles(self) -> "EventSeries":
        """Return a new EventSeries with no responsibles."""
        return EventSeries(
            repetition=self.repetition,
            club=self.club,
            responsible="",
            name=self.name,
            channel=self.channel,
            next_event=self.next_event,
            next_message=self.next_message,
            last_message_id=self.last_message_id,
        )

    # --- next-event helpers --------------------------------------------------

    def next_occurrence_from(self, reference: datetime | None = None) -> datetime | None:
        """
        Compute the next occurrence of `next_event` strictly after `reference` (or now).
        Returns None when repetition is NONE/CUSTOM without period logic.
        """
        ref = reference if reference is not None else datetime.now()
        new = self.next_event

        # If stored next_event is already after the reference, return it directly.
        if new > ref:
            return new

        # Determine period advancement function
        if self.repetition == RepetitionType.DAILY:
            step = lambda d: d + timedelta(days=1)
        elif self.repetition == RepetitionType.WEEKLY:
            step = lambda d: d + timedelta(days=7)
        elif self.repetition == RepetitionType.BIWEEKLY:
            step = lambda d: d + timedelta(days=14)
        elif self.repetition == RepetitionType.MONTHLY:
            step = lambda d: _add_months(d, 1)
        elif self.repetition == RepetitionType.YEARLY:
            step = lambda d: _add_years(d, 1)
        else:
            return None

        candidate = new
        max_iters = 1000
        iters = 0
        while candidate <= ref and iters < max_iters:
            candidate = step(candidate)
            iters += 1

        if iters >= max_iters:
            return None

        return candidate

    def with_advanced_next_event(self, reference: datetime | None = None) -> "EventSeries":
        """
        Return a new EventSeries with `next_event` advanced according to repetition so that it is
        strictly after `reference` (or now). Also sets `next_message` to the same value.
        """
        successor = self.next_occurrence_from(reference)
        if successor is None:
            return self
        return EventSeries(
            repetition=self.repetition,
            club=self.club,
            responsible=self.responsible,
            name=self.name,
            channel=self.channel,
            next_event=successor,
            next_message=successor,
            last_message_id=self.last_message_id,
        )

    def sync_next_message_to_event(self) -> "EventSeries":
        """Return a new EventSeries where next_message is set to next_event."""
        return EventSeries(
            repetition=self.repetition,
            club=self.club,
            responsible=self.responsible,
            name=self.name,
            channel=self.channel,
            next_event=self.next_event,
            next_message=self.next_event,
            last_message_id=self.last_message_id,
        )

    # --- IO helpers ----------------------------------------------------------

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EventSeries":
        """Construct from a dict (same shape as to_dict)."""
        repetition_raw = data["repetition"]
        club = data["club"]
        responsible = data.get("responsible", "")
        name = data["name"]
        channel_raw = data["channel"]

        if not isinstance(repetition_raw, str):
            raise TypeError("repetition must be a string")
        repetition = RepetitionType(repetition_raw)

        if not isinstance(club, str) or not club.strip():
            raise ValueError("club must be a non-empty string")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name must be a non-empty string")

        if isinstance(channel_raw, int):
            channel = channel_raw
        elif isinstance(channel_raw, str) and channel_raw.isdigit():
            channel = int(channel_raw)
        else:
            raise TypeError("channel must be an int")

        # parse datetimes (ISO strings) - next_event is required
        next_event_raw = data.get("next_event")
        if not isinstance(next_event_raw, str):
            raise ValueError("next_event is required and must be an ISO datetime string")
        next_event = datetime.fromisoformat(next_event_raw)

        next_message_raw = data.get("next_message")
        next_message = datetime.fromisoformat(next_message_raw) if isinstance(next_message_raw, str) else None

        last_message_id_raw = data.get("last_message_id")
        if last_message_id_raw is None:
            last_message_id = None
        elif isinstance(last_message_id_raw, int):
            last_message_id = last_message_id_raw
        elif isinstance(last_message_id_raw, str) and last_message_id_raw.isdigit():
            last_message_id = int(last_message_id_raw)
        else:
            raise TypeError("last_message_id must be an int or numeric string or null")

        return cls(
            repetition=repetition,
            club=club,
            responsible=responsible,
            name=name,
            channel=channel,
            next_event=next_event,
            next_message=next_message,
            last_message_id=last_message_id,
        )

    @classmethod
    def load_from_file(cls, filepath: str) -> "EventSeries":
        """Load EventSeries from file (keeps backwards compatibility with previous shape)."""
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(filepath)
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("file does not contain a JSON object")
        return cls.from_dict(data)