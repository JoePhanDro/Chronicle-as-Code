"""Locate, parse and validate timeline.yaml plus its evidence files."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from .evidence import MAX_EVIDENCE_BYTES, Blob, EvidenceRejected, check_file
from .models import Timeline

TIMELINE_NAME = "timeline.yaml"
# Evidence is copied next to index.html, so these names must not be overwritten.
RESERVED_OUTPUT_NAMES = {"index.html", "index.html.tmp"}
# One byte more than the limit, so that check_file can report files that are too large.
MAX_READ_BYTES = MAX_EVIDENCE_BYTES + 1


class TimelineError(Exception):
    """The timeline cannot be found, parsed or validated."""


def find_timeline(data_dir: Path) -> Path:
    if not data_dir.is_dir():
        raise TimelineError(f"Datenordner existiert nicht: {data_dir}")

    candidates = sorted(data_dir.rglob(TIMELINE_NAME))
    if not candidates:
        raise TimelineError(f"Keine {TIMELINE_NAME} unterhalb von {data_dir} gefunden")
    if len(candidates) > 1:
        listing = ", ".join(str(path) for path in candidates)
        raise TimelineError(f"Mehrere {TIMELINE_NAME} gefunden, bitte nur eine behalten: {listing}")
    return candidates[0]


def load_timeline(path: Path) -> Timeline:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise TimelineError(f"YAML-Syntaxfehler in {path}: {exc}") from exc

    try:
        return Timeline.model_validate(raw)
    except ValidationError as exc:
        raise TimelineError(f"Schema-Validierung fehlgeschlagen für {path}:\n{exc}") from exc


def load_evidence(timeline: Timeline, base_dir: Path) -> dict[str, Blob]:
    """Read and check every evidence file. Keys are the relative paths used in the YAML."""
    blobs: dict[str, Blob] = {}
    problems: list[str] = []
    for relative, source in collect_evidence(timeline, base_dir).items():
        with source.open("rb") as handle:
            data = handle.read(MAX_READ_BYTES)
        try:
            blobs[relative] = check_file(relative, data)
        except EvidenceRejected as exc:
            problems.append(str(exc))

    if problems:
        raise TimelineError("Ungültige Evidence-Dateien:\n  " + "\n  ".join(problems))
    return blobs


def collect_evidence(timeline: Timeline, base_dir: Path) -> dict[str, Path]:
    """Map each evidence path (relative, posix) to its absolute source file.

    Evidence must exist and must resolve (symlinks included) inside base_dir.
    """
    base = base_dir.resolve()
    found: dict[str, Path] = {}
    problems: list[str] = []

    for event in timeline.events:
        for item in event.evidence:
            source = (base / item.file).resolve()
            if item.file in RESERVED_OUTPUT_NAMES:
                problems.append(f"{event.key}: '{item.file}' ist für die Ausgabe reserviert")
            elif not source.is_relative_to(base):
                problems.append(f"{event.key}: '{item.file}' liegt außerhalb des Timeline-Ordners")
            elif not source.is_file():
                problems.append(f"{event.key}: Datei fehlt: '{item.file}'")
            else:
                found[item.file] = source

    if problems:
        raise TimelineError("Ungültige Evidence-Pfade:\n  " + "\n  ".join(problems))
    return found
