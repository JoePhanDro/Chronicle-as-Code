"""Strict Pydantic schema for timeline.yaml.

Every model forbids unknown keys, so a typo in the YAML fails loudly
instead of silently dropping data.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Phase(StrEnum):
    IDENTIFICATION = "Identification"
    EXPLOIT = "Exploit"
    ANALYSIS = "Analysis"
    CONTAINMENT = "Containment"
    MITIGATION = "Mitigation"
    RECOVERY = "Recovery"
    LESSONS_LEARNED = "Lessons Learned"


PHASE_COLORS: dict[Phase, str] = {
    Phase.IDENTIFICATION: "#ef4444",  # red
    Phase.EXPLOIT: "#ef4444",  # red
    Phase.ANALYSIS: "#eab308",  # yellow
    Phase.CONTAINMENT: "#f97316",  # orange
    Phase.MITIGATION: "#22c55e",  # green
    Phase.RECOVERY: "#10b981",  # emerald
    Phase.LESSONS_LEARNED: "#3b82f6",  # blue
}


class Status(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"


EvidenceType = Literal["screenshot", "log", "report", "other"]


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str = Field(min_length=1, description="Path relative to the timeline.yaml folder")
    type: EvidenceType
    description: str = ""

    @field_validator("file")
    @classmethod
    def _relative_path_only(cls, value: str) -> str:
        path = PurePosixPath(value.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or ":" in path.parts[0]:
            raise ValueError(f"evidence path must be relative and stay inside the data folder: {value!r}")
        return path.as_posix()


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]+$", description="Optional, generated if missing")
    t0: datetime = Field(description="ISO 8601 timestamp with timezone offset")
    phase: Phase
    actor: str = Field(min_length=1)
    system: str | None = None
    action: str = Field(min_length=1)
    status: Status
    triggered_by: str | None = Field(default=None, description="Event id or actor name that caused this event")
    details: str = Field(default="", description="Markdown body shown in the detail view")
    evidence: list[Evidence] = Field(default_factory=list)

    @field_validator("t0")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("t0 needs a timezone offset, e.g. 2026-10-14T08:12:00+02:00")
        return value.astimezone(UTC)


class Timeline(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    summary: str = ""
    events: list[Event] = Field(min_length=1)

    @model_validator(mode="after")
    def _assign_ids_and_check_references(self) -> Timeline:
        for index, event in enumerate(self.events, start=1):
            if event.id is None:
                event.id = f"evt-{index:03d}"

        ids = [event.id for event in self.events]
        duplicates = sorted({event_id for event_id in ids if ids.count(event_id) > 1})
        if duplicates:
            raise ValueError(f"duplicate event ids: {duplicates}")

        known_references = set(ids) | {event.actor for event in self.events}
        for event in self.events:
            if event.triggered_by and event.triggered_by not in known_references:
                raise ValueError(
                    f"event {event.id!r}: triggered_by {event.triggered_by!r} is neither an event id nor an actor"
                )
        return self
