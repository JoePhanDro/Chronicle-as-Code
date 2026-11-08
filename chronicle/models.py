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


class EventComment(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    created_at: datetime
    author: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=5000)

    @field_validator("created_at")
    @classmethod
    def _require_comment_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("comment created_at needs a timezone offset")
        return value.astimezone(UTC)

    @field_validator("text")
    @classmethod
    def _comment_line_ends(cls, value: str) -> str:
        return value.replace("\r\n", "\n").replace("\r", "\n").strip()


class Detection(BaseModel):
    """The detection that led to this event (issue #257): which rule or alert in the SIEM/EDR, at which rule
    version. Chronicle does not detect anything itself; it only records what the detecting system reported.
    At least rule_id or alert_id must be set."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    rule_id: str | None = None  # Sigma-ID or rule number in the SIEM
    rule_name: str | None = None  # free text
    rule_version: str | None = None  # version or commit of the rule at the time of the hit
    source: str | None = None  # the system that detected, e.g. Defender, Elastic
    alert_id: str | None = None  # alert ID or link to the alert

    @field_validator("rule_id", "rule_name", "rule_version", "source", "alert_id")
    @classmethod
    def _bounded_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value or len(value) > 200:
            raise ValueError("Erkennungs-Felder: 1 bis 200 Zeichen")
        return value

    @model_validator(mode="after")
    def _at_least_one_id(self) -> Detection:
        if not (self.rule_id or self.alert_id):
            raise ValueError("detection braucht mindestens rule_id oder alert_id")
        return self


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
    tags: list[str] = Field(default_factory=list, max_length=50)
    detection: Detection | None = Field(
        default=None, description="The rule/alert that led to this event (issue #257), if any"
    )
    comments: list[EventComment] = Field(default_factory=list, max_length=200)
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

    @field_validator("tags", mode="before")
    @classmethod
    def _tidy_tags(cls, value: object) -> object:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, list):
            return [item.strip() if isinstance(item, str) else item for item in value]
        return value

    @field_validator("tags")
    @classmethod
    def _tag_set(cls, value: list[str]) -> list[str]:
        if any(not item or len(item) > 100 for item in value):
            raise ValueError("Tags dürfen nicht leer sein und höchstens 100 Zeichen enthalten")
        return sorted(set(value), key=str.casefold)


TopicKind = Literal["incident", "vulnerability", "project", "special", "other"]


class Reporting(BaseModel):
    """The reporting obligation of a topic (issue #229): legal basis and the moment the incident became known.
    The stage deadlines (Frühwarnung, Meldung, Abschlussbericht) follow from the regulation and can be
    overridden per topic in hours. Chronicle only shows the deadlines and builds the reports, it never sends."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    regulation: Literal["nis2", "bsig", "dora", "dsgvo", "other"]
    known_at: datetime = Field(description="Zeitpunkt der Kenntnisnahme, ISO 8601 mit Zeitzone")
    deadlines: dict[str, float] = Field(
        default_factory=dict,
        description="Eigene Fristen pro Stufe in Stunden ab Kenntnisnahme, z. B. {'notification': 48}",
    )

    @field_validator("known_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("known_at braucht eine Zeitzone, z. B. 2026-10-14T08:00:00+02:00")
        return value.astimezone(UTC)

    @field_validator("deadlines")
    @classmethod
    def _check_deadlines(cls, value: dict[str, float]) -> dict[str, float]:
        from .reporting import MAX_DEADLINE_HOURS, STAGES

        wrong = sorted(set(value) - set(STAGES))
        if wrong:
            raise ValueError(f"Unbekannte Stufen: {', '.join(wrong)} (erlaubt: {', '.join(STAGES)})")
        if any(not 0 < hours <= MAX_DEADLINE_HOURS for hours in value.values()):
            raise ValueError("Fristen müssen größer als 0 und höchstens 10 Jahre sein")
        return value


NotificationTrigger = Literal["new_event", "status_change", "new_evidence", "rule_match"]
NOTIFICATION_TRIGGERS: tuple[str, ...] = ("new_event", "status_change", "new_evidence", "rule_match")

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})
EMAIL_PATTERN = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}(\.[^@\s]+)+$")


class WebhookConfig(BaseModel):
    """Webhook channel of a topic (issue #258): the URL is stored here, the HMAC secret comes from the
    environment (CHRONICLE_WEBHOOK_SECRET) and never lands in the YAML files that are in Git."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    url: str
    triggers: list[NotificationTrigger] = Field(default_factory=lambda: list(NOTIFICATION_TRIGGERS))

    @field_validator("url")
    @classmethod
    def _webhook_url(cls, value: str) -> str:
        from urllib.parse import urlsplit

        value = value.strip()
        if len(value) > 2000:
            raise ValueError("Webhook-URL: höchstens 2000 Zeichen")
        parsed = urlsplit(value)
        # https only; plain http just for a local test receiver. The URL is configured by admins, but the
        # server makes the request, so it must not point anywhere without TLS.
        if parsed.scheme == "https":
            pass
        elif parsed.scheme == "http" and parsed.hostname and parsed.hostname.lower() in LOOPBACK_HOSTS:
            pass
        else:
            raise ValueError("Webhook-URL braucht https (http nur für localhost/127.0.0.1)")
        if not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Webhook-URL ohne Zugangsdaten und mit Host, z. B. https://hooks.example.com/abc")
        return value

    @field_validator("triggers")
    @classmethod
    def _trigger_set(cls, value: list[str]) -> list[str]:
        return sorted(set(value))


class EmailConfig(BaseModel):
    """E-mail channel of a topic (issue #258): recipients are stored here, the SMTP server and credentials
    come from the environment (CHRONICLE_SMTP_*), so no secrets land in the YAML files that are in Git."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    to: list[str] = Field(min_length=1, max_length=20)
    triggers: list[NotificationTrigger] = Field(default_factory=lambda: list(NOTIFICATION_TRIGGERS))

    @field_validator("to")
    @classmethod
    def _recipients(cls, value: list[str]) -> list[str]:
        cleaned = []
        for item in value:
            item = item.strip()
            if not item or len(item) > 200 or not EMAIL_PATTERN.fullmatch(item):
                raise ValueError(f"Keine gültige E-Mail-Adresse: {item[:60]!r}")
            cleaned.append(item)
        return sorted(set(cleaned))

    @field_validator("triggers")
    @classmethod
    def _trigger_set(cls, value: list[str]) -> list[str]:
        return sorted(set(value))


class Notifications(BaseModel):
    """The notification channels of a topic (issue #258). Delivery is active per trigger; the secrets
    (webhook HMAC, SMTP credentials) live in the environment, not in the topic file."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    webhook: WebhookConfig | None = None
    email: EmailConfig | None = None

    @model_validator(mode="after")
    def _at_least_one_channel(self) -> Notifications:
        if not (self.webhook or self.email):
            raise ValueError("notifications braucht mindestens webhook oder email")
        return self


Severity = Literal["low", "medium", "high", "critical"]
SEVERITIES: tuple[str, ...] = ("low", "medium", "high", "critical")
SEVERITY_LABELS: dict[str, str] = {"low": "niedrig", "medium": "mittel", "high": "hoch", "critical": "kritisch"}

TlpLevel = Literal["CLEAR", "GREEN", "AMBER", "RED"]
TLP_LEVELS: tuple[str, ...] = ("CLEAR", "GREEN", "AMBER", "RED")

INCIDENT_TEXT_MAX = 2000


class IncidentClassification(BaseModel):
    """The classification of an incident topic (issue #225): severity, TLP for sharing reports, impact,
    root cause and measures. Every field is optional; a topic without the block is unchanged. The levels are
    fixed (like Status and Phase), not configurable per instance."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    severity: Severity | None = None
    tlp: TlpLevel | None = None
    impact: str = ""
    affected_systems: list[str] = Field(default_factory=list, max_length=50)
    data_categories: list[str] = Field(default_factory=list, max_length=50)
    affected_persons: int | None = Field(default=None, ge=0, le=10**9, description="geschätzte Zahl der Betroffenen")
    cause: str = ""
    measures: str = ""

    @field_validator("impact", "cause", "measures")
    @classmethod
    def _bounded_text(cls, value: str) -> str:
        value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
        if len(value) > INCIDENT_TEXT_MAX:
            raise ValueError(f"höchstens {INCIDENT_TEXT_MAX} Zeichen")
        return value

    @field_validator("affected_systems", "data_categories", mode="before")
    @classmethod
    def _tidy_list(cls, value: object) -> object:
        # Forms send "" for nothing and a comma-separated string.
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, list):
            return [item.strip() if isinstance(item, str) else item for item in value]
        return value

    @field_validator("affected_systems", "data_categories")
    @classmethod
    def _entry_set(cls, value: list[str]) -> list[str]:
        if any(not item or len(item) > 100 for item in value):
            raise ValueError("Einträge dürfen nicht leer sein und höchstens 100 Zeichen enthalten")
        return sorted(set(value), key=str.casefold)

    def stored(self) -> dict:
        """The block as it is written to the YAML file: empty fields are left out, so a topic without real
        content gets no `incident:` key and old files stay byte-identical."""
        data = self.model_dump(mode="json", exclude_none=True)
        return {name: value for name, value in data.items() if value not in ("", [])}


def incident_rows(incident: dict | None) -> list[tuple[str, str]]:
    """The set fields of an incident classification as label/value rows for display (issue #225)."""
    inc = incident or {}
    if not inc:
        return []
    rows = []
    if inc.get("severity"):
        rows.append(("Schweregrad", SEVERITY_LABELS.get(inc["severity"], inc["severity"])))
    if inc.get("tlp"):
        rows.append(("TLP", f"TLP:{inc['tlp']}"))
    if inc.get("impact"):
        rows.append(("Auswirkungen", inc["impact"]))
    if inc.get("affected_systems"):
        rows.append(("Betroffene Systeme", ", ".join(inc["affected_systems"])))
    if inc.get("data_categories"):
        rows.append(("Datenkategorien", ", ".join(inc["data_categories"])))
    if inc.get("affected_persons") is not None:
        rows.append(("Betroffene Personen (geschätzt)", str(inc["affected_persons"])))
    if inc.get("cause"):
        rows.append(("Ursache", inc["cause"]))
    if inc.get("measures"):
        rows.append(("Maßnahmen", inc["measures"]))
    return rows


class Topic(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
    title: str = Field(min_length=1)
    kind: TopicKind = "other"
    summary: str = ""
    reporting: Reporting | None = Field(default=None, description="Meldepflicht des Topics (issue #229)")
    notifications: Notifications | None = Field(default=None, description="Benachrichtigungskanäle des Topics (issue #258)")
    incident: IncidentClassification | None = Field(default=None, description="Klassifikation des Incidents (issue #225)")


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
