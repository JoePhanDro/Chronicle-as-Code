"""Meldepflicht-Fristen eines Topics (issue #229): NIS2, BSIG, DORA und DSGVO haben gestufte Meldungen
ab Kenntnisnahme. Chronicle versendet nichts automatisch — es zeigt die Fristen an und erzeugt die
gestuften Berichte (Frühwarnung, Meldung, Abschlussbericht) als PDF.

The deadlines are reference values in hours after `known_at`, overridable per topic (reporting.deadlines);
the legal text decides in the end, the operator adjusts the topic. A topic without a configured stage has
no timer for that stage.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

STAGES = ("early_warning", "notification", "final_report")
STAGE_LABELS = {"early_warning": "Frühwarnung", "notification": "Meldung", "final_report": "Abschlussbericht"}
REGULATIONS = ("nis2", "bsig", "dora", "dsgvo", "other")
REGULATION_LABELS = {
    "nis2": "NIS2 (Art. 23)",
    "bsig": "BSIG",
    "dora": "DORA",
    "dsgvo": "DSGVO (Art. 33)",
    "other": "Sonstige",
}
# Reference values in hours after knowledge of the incident; DORA/NIS2 stage their reports, BSIG/DSGVO
# know one notification deadline. Per topic overridable via reporting.deadlines.
DEFAULT_DEADLINE_HOURS: dict[str, dict[str, float]] = {
    "nis2": {"early_warning": 24, "notification": 72, "final_report": 720},  # 24 h, 72 h, 1 Monat
    "bsig": {"notification": 24},
    "dora": {"early_warning": 4, "notification": 24, "final_report": 720},
    "dsgvo": {"notification": 72},
    "other": {},
}
MAX_DEADLINE_HOURS = 24 * 366 * 10


def stage_hours(reporting: dict) -> dict[str, float]:
    """The effective hours per stage: regulation defaults, overridden by the topic's own values."""
    hours = dict(DEFAULT_DEADLINE_HOURS.get(reporting.get("regulation") or "other", {}))
    for stage, value in (reporting.get("deadlines") or {}).items():
        hours[stage] = float(value)
    return hours


def stage_deadlines(reporting: dict, now: datetime | None = None) -> list[dict]:
    """One entry per stage: label, hours, absolute deadline, remaining seconds, overdue flag.
    Stages without a configured deadline get deadline None."""
    now = now or datetime.now(UTC)
    known_at = datetime.fromisoformat(reporting["known_at"])
    entries = []
    for stage in STAGES:
        hours = stage_hours(reporting).get(stage)
        deadline = known_at + timedelta(hours=hours) if hours else None
        remaining = (deadline - now).total_seconds() if deadline else None
        entries.append({
            "stage": stage, "label": STAGE_LABELS[stage], "hours": hours,
            "deadline": deadline, "remaining": remaining, "overdue": deadline is not None and remaining < 0,
        })
    return entries


def worst_status(entries: list[dict], now: datetime | None = None) -> str:
    """For badges: "overdue" when any stage is past, "due" when one is due within 24 h, else "ok" (or "none")."""
    timed = [entry for entry in entries if entry["deadline"] is not None]
    if not timed:
        return "none"
    if any(entry["overdue"] for entry in timed):
        return "overdue"
    now = now or datetime.now(UTC)
    if any(entry["remaining"] is not None and entry["remaining"] <= 24 * 3600 for entry in timed):
        return "due"
    return "ok"


def human_remaining(remaining: float | None) -> str:
    """The remaining time as a short text, e.g. "2 d 3 h", "5 h", "42 min"; negative means overdue."""
    if remaining is None:
        return "—"
    seconds = abs(int(remaining))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes = seconds // 60
    if days:
        text = f"{days} d {hours} h"
    elif hours:
        text = f"{hours} h {minutes} min"
    else:
        text = f"{minutes} min"
    return f"überfällig seit {text}" if remaining < 0 else f"noch {text}"


def missing_fields(reporting: dict, events: list[dict], stage: str) -> list[str]:
    """What a report of this stage still lacks, named before it is generated (issue #229)."""
    missing: list[str] = []
    if not events:
        missing.append("Keine Events im Topic")
    if stage in ("notification", "final_report"):
        if not any(event.get("detection") or event.get("techniques") for event in events):
            missing.append("Weder Erkennung (detection) noch ATT&CK-Techniken an den Events")
    if stage == "final_report":
        if not any(event.get("phase") in ("Recovery", "Lessons Learned") for event in events):
            missing.append("Keine Events in den Phasen Recovery / Lessons Learned (Maßnahmen, Wiederherstellung)")
    return missing
