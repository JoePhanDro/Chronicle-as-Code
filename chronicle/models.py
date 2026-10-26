"""Strict Pydantic schema for timeline.yaml.

Every model forbids unknown keys, so a typo in the YAML fails loudly
instead of silently dropping data.
"""

from __future__ import annotations

from collections import Counter
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
    ACTIVITY = "Activity"  # neutral: for events outside the incident phases


PHASE_COLORS: dict[Phase, str] = {
    Phase.IDENTIFICATION: "#ef4444",  # red
    Phase.EXPLOIT: "#ef4444",  # red
    Phase.ANALYSIS: "#eab308",  # yellow
    Phase.CONTAINMENT: "#f97316",  # orange
    Phase.MITIGATION: "#22c55e",  # green
    Phase.RECOVERY: "#10b981",  # emerald
    Phase.LESSONS_LEARNED: "#3b82f6",  # blue
    Phase.ACTIVITY: "#94a3b8",  # slate
}


class Status(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"


# The status words the interface shows; the stored values stay English. Phase names stay English too: they are the terms of the data model.
STATUS_LABELS: dict[Status, str] = {
    Status.OPEN: "offen",
    Status.IN_PROGRESS: "in Arbeit",
    Status.RESOLVED: "abgeschlossen",
}

EvidenceType = Literal["screenshot", "log", "report", "other"]


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    file: str = Field(min_length=1, description="Path relative to the timeline.yaml folder")
    type: EvidenceType
    description: str = ""

    @field_validator("file")
    @classmethod
    def _relative_path_only(cls, value: str) -> str:
        path = PurePosixPath(value.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or ":" in path.parts[0]:
            raise ValueError("evidence path must be relative and stay inside the data folder")
        return path.as_posix()


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    key: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$",
        description="Stable, globally unique key. Re-importing the same key updates the event.",
    )
    t0: datetime = Field(description="ISO 8601 timestamp with timezone offset")
    phase: Phase
    actor: str = Field(min_length=1)
    system: str | None = None
    action: str = Field(min_length=1)
    status: Status
    triggered_by: list[str] = Field(
        default_factory=list,
        description="Keys of the events that caused this event. Any number, including none (issue #87)",
    )
    details: str = Field(default="", description="Markdown body shown in the detail view")
    evidence: list[Evidence] = Field(default_factory=list)

    @field_validator("t0")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("t0 needs a timezone offset, e.g. 2026-10-14T08:12:00+02:00")
        return value.astimezone(UTC)

    @field_validator("triggered_by", mode="before")
    @classmethod
    def _one_key_or_list(cls, value: object) -> object:
        # Files written before issue #87 hold a single key, or nothing. Forms send "" for nothing.
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [value]
        return value

    @field_validator("triggered_by")
    @classmethod
    def _as_set(cls, value: list[str]) -> list[str]:
        # The causes form a set: order and repeats carry no meaning, so they are stored sorted and unique.
        if any(not key for key in value):
            raise ValueError("triggered_by enthält einen leeren Key")
        return sorted(set(value))


TopicKind = Literal["incident", "vulnerability", "project", "special", "other"]


class Topic(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
    title: str = Field(min_length=1)
    kind: TopicKind = "other"
    summary: str = ""


class TopicCreate(Topic):
    """Topic for a new record. Without a key the server generates one (see new_topic_key)."""

    key: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


class Timeline(BaseModel):
    """One topic file: the topic, the events it defines and references to events defined elsewhere."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    topic: Topic
    events: list[Event] = Field(default_factory=list)
    refs: list[str] = Field(default_factory=list, description="Keys of events defined in other topic files")

    @model_validator(mode="after")
    def _check_local_keys(self) -> Timeline:
        keys = [event.key for event in self.events]
        counts = Counter(keys)  # list.count per key was quadratic: seconds for 10,000 events (issue #125)
        duplicates = sorted(key for key, count in counts.items() if count > 1)
        if duplicates:
            raise ValueError(f"duplicate event keys: {duplicates}")
        overlap = sorted(set(keys) & set(self.refs))
        if overlap:
            raise ValueError(f"events defined here cannot also be referenced: {overlap}")
        return self
