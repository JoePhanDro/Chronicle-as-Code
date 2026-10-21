"""Input for writing events: the same rules for the UI, the API and imports."""

from __future__ import annotations

from pydantic import Field

from .models import Event


class EventWrite(Event):
    """Event as sent by the UI or API. `topic` is the home topic, `topics` lists all topics it belongs to."""

    topic: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
    topics: list[str] = Field(default_factory=list)


class EventUpdate(EventWrite):
    version: int = Field(ge=1, description="Version the change is based on")


def event_of(body: EventWrite) -> Event:
    return Event.model_validate(body.model_dump(exclude={"topic", "topics", "version"}))


def topic_list(body: EventWrite) -> list[str]:
    """Home topic first, then the others, without duplicates."""
    return list(dict.fromkeys([body.topic, *body.topics]))
