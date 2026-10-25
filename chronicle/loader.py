"""Locate, parse and validate timeline.yaml plus its evidence files."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import ValidationError

from .evidence import MAX_EVIDENCE_BYTES, Blob, EvidenceRejected, check_file
from .models import Timeline
from .yamlscan import MAX_YAML_BYTES, TOO_LARGE, YamlRejected, describe_validation_error, load_yaml, one_line

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
TOPIC_SUFFIXES = tuple(pattern[1:] for pattern in TOPIC_FILE_PATTERNS)


MAX_LISTED_LINKS = 10


def find_topic_files(data_dir: Path) -> list[Path]:
    """Every YAML file below the folder. Each file is one topic (ADR 0005).

    The folder is walked once. A link anywhere below it (to a file or a folder) is refused, because the app would read
    or write outside the folder through it. Only regular files count as topic files. The Git folder in the root is
    not looked at.
    """
    if not data_dir.is_dir():
        raise TimelineError(f"Datenordner existiert nicht: {data_dir}")

    files: list[Path] = []
    links: list[str] = []
    pending = [data_dir]
    while pending:
        folder = pending.pop()
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    if entry.is_symlink():
                        links.append(Path(entry.path).relative_to(data_dir).as_posix())
                    elif entry.is_dir(follow_symlinks=False):
                        if not (folder == data_dir and entry.name == ".git"):
                            pending.append(Path(entry.path))
                    elif entry.name.endswith(TOPIC_SUFFIXES) and entry.is_file(follow_symlinks=False):
                        files.append(Path(entry.path))
        except OSError as exc:  # only the path is named: the error text of the system may hold file names
            where = folder.relative_to(data_dir).as_posix()
            raise TimelineError(f"Ordner nicht lesbar: {where} ({exc.strerror})") from exc
    if links:
        links.sort()
        more = f" und {len(links) - MAX_LISTED_LINKS} weitere" if len(links) > MAX_LISTED_LINKS else ""
        raise TimelineError("Links sind im Datenordner nicht erlaubt: " + ", ".join(links[:MAX_LISTED_LINKS]) + more)
    if not files:
        raise TimelineError(f"Keine YAML-Datei unterhalb von {data_dir} gefunden")
    return sorted(files)


def link_below(base: Path, path: Path) -> Path | None:
    """The first part of path below base (the path itself or a folder on the way) that is a symbolic link.

    base itself may be a link: the data folder can be a mount.
    """
    if not path.is_relative_to(base):
        return None
    current = base
    for part in path.relative_to(base).parts:
        current = current / part
        if current.is_symlink():
            return current
    return None


def parse_timeline(text: str, where: str) -> Timeline:
    """A topic file from its text. `where` names the file in the error messages."""
    where = one_line(where)
    try:
        raw = load_yaml(text)
    except YamlRejected as exc:
        raise TimelineError(f"{where}: {exc}") from exc

    try:
        return Timeline.model_validate(raw)
    except ValidationError as exc:
        raise TimelineError(f"Schema-Validierung fehlgeschlagen für {where}: {describe_validation_error(exc)}") from exc


def load_timeline(path: Path) -> Timeline:
    with path.open("rb") as handle:
        data = handle.read(MAX_YAML_BYTES + 1)  # one byte more than the limit shows that the file is too large
    return parse_timeline(decode_topic_bytes(data, str(path)), str(path))


def decode_topic_bytes(data: bytes, where: str) -> str:
    """The text of a topic file read from disk or from Git. Refuses what is too large or no UTF-8, without its content."""
    if len(data) > MAX_YAML_BYTES:
        raise TimelineError(f"{one_line(where)}: {TOO_LARGE}")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TimelineError(f"{one_line(where)}: Datei ist kein gültiges UTF-8 (Byte {exc.start})") from exc


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

    Evidence must exist, be no link and lie inside base_dir.
    """
    base = base_dir.resolve()
    found: dict[str, Path] = {}
    problems: list[str] = []

    for event in timeline.events:
        for item in event.evidence:
            source = (base / item.file).resolve()
            if link_below(base, base / item.file) is not None:
                problems.append(f"{event.key}: '{item.file}' ist ein Link, Links sind nicht erlaubt")
            elif item.file in RESERVED_OUTPUT_NAMES:
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
