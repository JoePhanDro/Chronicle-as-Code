"""Strict Pydantic schema for timeline.yaml.

Every model forbids unknown keys, so a typo in the YAML fails loudly
instead of silently dropping data.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TECHNIQUE_ID = re.compile(r"T\d{4}(\.\d{3})?")


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
    techniques: list[str] = Field(
        default_factory=list,
        max_length=50,
        description="MITRE ATT&CK technique IDs, e.g. T1190 or T1059.001. Any number, including none (ADR 0026)",
    )
    details: str = Field(default="", description="Markdown body shown in the detail view")
    evidence: list[Evidence] = Field(default_factory=list)

    @field_validator("details")
    @classmethod
    def _unix_line_ends(cls, value: str) -> str:
        # Browsers send a textarea with CRLF. A YAML block scalar reads that back as LF, so a file would never match
        # what was written. The text is stored with LF.
        return value.replace("\r\n", "\n").replace("\r", "\n")

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

    @field_validator("techniques", mode="before")
    @classmethod
    def _tidy_techniques(cls, value: object) -> object:
        # Forms send "" for nothing. IDs are compared in upper case, so t1190 is T1190.
        if value is None or value == "":
            return []
        if isinstance(value, list):
            return [item.strip().upper() if isinstance(item, str) else item for item in value]
        return value

    @field_validator("techniques")
    @classmethod
    def _technique_ids(cls, value: list[str]) -> list[str]:
        # A set, like triggered_by: stored sorted and unique. Only the format is checked, there is no catalogue (ADR 0026).
        wrong = [item for item in value if not TECHNIQUE_ID.fullmatch(item)]
        if wrong:
            raise ValueError(f"Keine gültige ATT&CK-Technik-ID (z. B. T1190 oder T1059.001): {', '.join(map(repr, wrong[:3]))}")
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
