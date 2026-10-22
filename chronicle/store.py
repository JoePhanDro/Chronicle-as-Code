"""Writing topic files (ADR 0008, issue #89). The server is the only writer of its data folder.

A write is checked against the version the caller read, is atomic (temporary file, then rename), and is only
done when the new text reads back as the same timeline. The version of a file is the SHA-256 of its bytes, so an
edit made in an editor or by `git pull` is noticed, and the caller gets a conflict instead of overwriting it.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import yaml

from .exporter import _Dumper
from .loader import _YAML_LOADER, TimelineError
from .models import Timeline
from .schema import SCHEMA_URL

LOCK_FILE = ".chronicle.lock"
HEADER = f"# yaml-language-server: $schema={SCHEMA_URL}\n"


class StoreError(TimelineError):
    """The file cannot be written as requested."""


class VersionConflict(StoreError):
    """The file changed since the caller read it. Nothing was written."""


def file_version(path: Path) -> str:
    """The version of a topic file: the SHA-256 of its bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump_timeline(timeline: Timeline) -> str:
    """The file text for a timeline. Empty optional fields are left out, so the file holds only what is set."""
    return HEADER + yaml.dump(_document(timeline), Dumper=_Dumper, allow_unicode=True, sort_keys=False,
                              width=float("inf"))


def write_timeline(path: Path, timeline: Timeline, *, expected_version: str | None) -> str:
    """Write the timeline to path. Returns the new version.

    expected_version is the version the caller read, or None to create a new file. A file that changed in the
    meantime, or a file that exists when None is given, raises VersionConflict and is left alone.
    """
    text = dump_timeline(timeline)
    _verify_read_back(text, timeline)
    with folder_lock(path.parent):
        current = file_version(path) if path.exists() else None
        if current != expected_version:
            raise VersionConflict(
                f"{path.name} wurde inzwischen geändert. Bitte neu laden, bevor du speicherst."
                if expected_version is not None
                else f"{path.name} existiert bereits."
            )
        _atomic_write(path, text.encode("utf-8"))
    return file_version(path)


@contextmanager
def folder_lock(folder: Path) -> Iterator[None]:
    """One writer at a time for a data folder. The lock file is not a topic file, so the loader ignores it."""
    with (folder / LOCK_FILE).open("a") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _verify_read_back(text: str, timeline: Timeline) -> None:
    """The text must read back as exactly the timeline it was made from. Otherwise nothing is written."""
    try:
        read_back = Timeline.model_validate(yaml.load(text, Loader=_YAML_LOADER))
    except (yaml.YAMLError, ValueError) as exc:
        raise StoreError(f"Die Datei wäre nicht lesbar, deshalb wird nichts geschrieben: {exc}") from exc
    if read_back != timeline:
        raise StoreError("Die Datei würde sich beim Lesen anders verhalten als das Modell, deshalb wird nichts geschrieben.")


def _atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _document(timeline: Timeline) -> dict:
    topic: dict = {"key": timeline.topic.key, "title": timeline.topic.title, "kind": timeline.topic.kind}
    if timeline.topic.summary:
        topic["summary"] = timeline.topic.summary
    document: dict = {"topic": topic}

    events = []
    for event in timeline.events:
        item: dict = {
            "key": event.key,
            "t0": event.t0.isoformat(),
            "phase": event.phase.value,
            "actor": event.actor,
        }
        if event.system:
            item["system"] = event.system
        item["action"] = event.action
        item["status"] = event.status.value
        if event.triggered_by:
            item["triggered_by"] = list(event.triggered_by)
        if event.details:
            item["details"] = event.details
        if event.evidence:
            item["evidence"] = [_evidence_document(evidence) for evidence in event.evidence]
        events.append(item)
    if events:
        document["events"] = events
    if timeline.refs:
        document["refs"] = list(timeline.refs)
    return document


def _evidence_document(evidence) -> dict:
    item: dict = {"file": evidence.file, "type": evidence.type}
    if evidence.description:
        item["description"] = evidence.description
    return item
