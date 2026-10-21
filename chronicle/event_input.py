"""Input for writing events: the same rules for the UI, the API and imports."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator

from .models import Event

# Zones the event form offers. Times are stored in UTC, the form shows and enters them in one of these zones.
EVENT_TIMEZONES = ("Europe/Berlin", "UTC")
DEFAULT_TIMEZONE = EVENT_TIMEZONES[0]


class EventWrite(Event):
    """Event as sent by the UI or API. `topic` is the home topic, `topics` lists all topics it belongs to."""

    topic: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
    topics: list[str] = Field(default_factory=list)

    @field_validator("evidence")
    @classmethod
    def _no_inline_evidence(cls, value: list) -> list:
        # Files are uploaded separately, so that their content is checked and stored (ADR 0004).
        if value:
            raise ValueError("Evidence wird nicht im Event-Body gesendet, sondern als Datei hochgeladen")
        return value


class EventCreate(EventWrite):
    """Event for a new record. Without a key the server generates one (see new_event_key)."""

    key: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


class EventUpdate(EventWrite):
    version: int = Field(ge=1, description="Version the change is based on")


def new_event_key() -> str:
    """Key for an event created in the UI: UTC date and a random part, e.g. ev-20261021-3fa9c2e1."""
    return f"ev-{datetime.now(UTC):%Y%m%d}-{secrets.token_hex(4)}"


def event_of(body: EventWrite) -> Event:
    return Event.model_validate(body.model_dump(exclude={"topic", "topics", "version"}))


def topic_list(body: EventWrite) -> list[str]:
    """Home topic first, then the others, without duplicates."""
    return list(dict.fromkeys([body.topic, *body.topics]))


def t0_from_parts(day: str, hour: str, minute: str, zone: str) -> str:
    """Date and time picked in the form, as ISO 8601 with offset: the input EventWrite.t0 expects."""
    if zone not in EVENT_TIMEZONES:
        raise ValueError("Bitte eine der angebotenen Zeitzonen wählen")
    if not (day and hour and minute):
        raise ValueError("Bitte Datum, Stunde und Minute angeben")
    try:
        local = datetime.fromisoformat(f"{day}T{hour}:{minute}:00")
    except ValueError as exc:
        raise ValueError("Das Datum ist ungültig") from exc
    return local.replace(tzinfo=ZoneInfo(zone)).isoformat()


def t0_parts(t0: str, zone: str = DEFAULT_TIMEZONE) -> dict[str, str]:
    """A stored timestamp split into the form's fields, shown in `zone`."""
    local = datetime.fromisoformat(t0).astimezone(ZoneInfo(zone))
    return {
        "t0_date": local.date().isoformat(),
        "t0_hour": f"{local.hour:02d}",
        "t0_minute": f"{local.minute:02d}",
        "t0_zone": zone,
    }
