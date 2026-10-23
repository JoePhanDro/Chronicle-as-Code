"""Locate, parse and validate timeline.yaml plus its evidence files."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from .evidence import MAX_EVIDENCE_BYTES, Blob, EvidenceRejected, check_file
from .models import Timeline

# Evidence is copied next to index.html, so these names must not be overwritten.
RESERVED_OUTPUT_NAMES = {"index.html", "index.html.tmp"}
# One byte more than the limit, so that check_file can report files that are too large.
MAX_READ_BYTES = MAX_EVIDENCE_BYTES + 1


class TimelineError(Exception):
    """The timeline cannot be found, parsed or validated."""


class CheckFailed(TimelineError):
    """Several problems were found in the topic files. `problems` lists each one, so all of them can be shown at once."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("\n".join(problems))


TOPIC_FILE_PATTERNS = ("*.yaml", "*.yml")  # every file the loader reads as one topic


def find_topic_files(data_dir: Path) -> list[Path]:
    """Every YAML file below the folder. Each file is one topic (ADR 0005)."""
    if not data_dir.is_dir():
        raise TimelineError(f"Datenordner existiert nicht: {data_dir}")

    files = sorted({path for pattern in TOPIC_FILE_PATTERNS for path in data_dir.rglob(pattern)})
    if not files:
        raise TimelineError(f"Keine YAML-Datei unterhalb von {data_dir} gefunden")
    return files


# The C loader (libyaml) reads the same safe subset about eight times faster than the pure-Python one (issue #96).
_YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def parse_timeline(text: str, where: str) -> Timeline:
    """A topic file from its text. `where` names the file in the error messages."""
    try:
        raw = yaml.load(text, Loader=_YAML_LOADER)
    except yaml.YAMLError as exc:
        raise TimelineError(f"YAML-Syntaxfehler in {where}: {exc}") from exc

    try:
        return Timeline.model_validate(raw)
    except ValidationError as exc:
        raise TimelineError(f"Schema-Validierung fehlgeschlagen für {where}:\n{exc}") from exc


def load_timeline(path: Path) -> Timeline:
    return parse_timeline(path.read_text(encoding="utf-8"), str(path))


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
