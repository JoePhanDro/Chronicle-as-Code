"""Writing topic files (ADR 0008, issues #89 and #97). The server is the only writer of its data folder.

A write is checked against the version the caller read, is atomic (temporary file, then rename), and is only
done when the new text reads back as the expected timeline. The version of a file is the SHA-256 of its bytes, so
an edit made in an editor or by `git pull` is noticed, and the caller gets a conflict instead of overwriting it.

Changes to an existing file keep what the file already has: comments, key order and layout. Only the fields that
changed are touched (ruamel.yaml round trip).
"""

from __future__ import annotations

import fcntl
import hashlib
import io
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import yaml
from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import LiteralScalarString

from .loader import _YAML_LOADER, TimelineError
from .models import Event, Evidence, Timeline, Topic
from .schema import SCHEMA_URL

LOCK_FILE = ".chronicle.lock"
HEADER = f"# yaml-language-server: $schema={SCHEMA_URL}\n"  # only for new files; existing files keep their header


class StoreError(TimelineError):
    """The file cannot be written as requested."""


class VersionConflict(StoreError):
    """The file changed since the caller read it. Nothing was written."""


class EventNotInFile(StoreError):
    """The event is not defined in the file it was looked up in."""


def file_version(path: Path) -> str:
    """The version of a topic file: the SHA-256 of its bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump_timeline(timeline: Timeline) -> str:
    """The text of a new topic file. Empty optional fields are left out, so the file holds only what is set."""
    stream = io.StringIO()
    _yaml().dump(_document(timeline), stream)
    return HEADER + stream.getvalue()


def write_timeline(path: Path, timeline: Timeline, *, expected_version: str | None) -> str:
    """Write a whole timeline to path. Returns the new version.

    expected_version is the version the caller read, or None to create a new file.
    """
    text = dump_timeline(timeline)
    _verify_read_back(text, timeline)
    with folder_lock(path.parent):
        _check_version(path, expected_version)
        _atomic_write(path, text.encode("utf-8"))
    return file_version(path)


def update_event_in_file(path: Path, key: str, event: Event, *, expected_version: str) -> str:
    """Change the editable fields of one event in its file. Everything else in the file stays as it is.

    The evidence of the event is not touched here. Returns the new version of the file.
    """
    with folder_lock(path.parent):
        _check_version(path, expected_version)
        text = path.read_text(encoding="utf-8")
        current = _read_model(text)
        old = next((item for item in current.events if item.key == key), None)
        if old is None:
            raise EventNotInFile(f"Event {key!r} steht nicht in {path.name}.")

        document = _yaml().load(text)
        item = next(entry for entry in document["events"] if entry["key"] == key)
        _set_changed_fields(item, old, event)
        stream = io.StringIO()
        _yaml().dump(document, stream)

        expected = current.model_copy(update={"events": [
            event.model_copy(update={"evidence": old.evidence}) if known.key == key else known
            for known in current.events
        ]})
        _verify_read_back(stream.getvalue(), expected)
        _atomic_write(path, stream.getvalue().encode("utf-8"))
    return file_version(path)


def create_topic_file(path: Path, topic: Topic) -> str:
    """Create the file of a new topic, without events. Refused if the file exists. Returns its version."""
    return write_timeline(path, Timeline(topic=topic), expected_version=None)


def update_topic_in_file(path: Path, topic: Topic, *, expected_version: str) -> str:
    """Change the title, kind and summary of the topic a file defines. The key does not change. Returns the new version."""
    with folder_lock(path.parent):
        _check_version(path, expected_version)
        text = path.read_text(encoding="utf-8")
        current = _read_model(text)
        if current.topic.key != topic.key:
            raise StoreError(f"{path.name} gehört zum Topic {current.topic.key!r}, nicht zu {topic.key!r}.")

        document = _yaml().load(text)
        section = document["topic"]
        section["title"] = topic.title
        section["kind"] = topic.kind
        _put(section, "summary", _text(topic.summary) or None)
        stream = io.StringIO()
        _yaml().dump(document, stream)

        _verify_read_back(stream.getvalue(), current.model_copy(update={"topic": topic}))
        _atomic_write(path, stream.getvalue().encode("utf-8"))
    return file_version(path)


def add_event_to_file(path: Path, event: Event, *, expected_version: str) -> str:
    """Append a new event to a topic file. Returns the new version of the file."""
    with folder_lock(path.parent):
        _check_version(path, expected_version)
        text = path.read_text(encoding="utf-8")
        current = _read_model(text)
        if any(known.key == event.key for known in current.events):
            raise StoreError(f"Event {event.key!r} steht bereits in {path.name}.")

        document = _yaml().load(text)
        document.setdefault("events", []).append(_event_document(event))
        stream = io.StringIO()
        _yaml().dump(document, stream)

        _verify_read_back(stream.getvalue(), current.model_copy(update={"events": [*current.events, event]}))
        _atomic_write(path, stream.getvalue().encode("utf-8"))
    return file_version(path)


def remove_event_from_file(path: Path, key: str, *, expected_version: str) -> str:
    """Remove one event from a topic file. Its lines go with it; everything else stays. Returns the new version."""
    with folder_lock(path.parent):
        _check_version(path, expected_version)
        text = path.read_text(encoding="utf-8")
        current = _read_model(text)
        if not any(known.key == key for known in current.events):
            raise EventNotInFile(f"Event {key!r} steht nicht in {path.name}.")

        document = _yaml().load(text)
        document["events"] = [entry for entry in document["events"] if entry["key"] != key]
        if not document["events"]:
            del document["events"]
        stream = io.StringIO()
        _yaml().dump(document, stream)

        _verify_read_back(stream.getvalue(), current.model_copy(update={
            "events": [known for known in current.events if known.key != key],
        }))
        _atomic_write(path, stream.getvalue().encode("utf-8"))
    return file_version(path)


def set_ref_in_file(path: Path, key: str, *, present: bool, expected_version: str) -> str:
    """Add or remove the link to an event defined in another topic file (a key under refs). Returns the new version.

    An event cannot be linked to the topic it is defined in; the model refuses that on read back.
    """
    with folder_lock(path.parent):
        _check_version(path, expected_version)
        text = path.read_text(encoding="utf-8")
        current = _read_model(text)
        if present == (key in current.refs):
            return file_version(path)

        document = _yaml().load(text)
        if present:
            document.setdefault("refs", []).append(key)
            refs = [*current.refs, key]
        else:
            document["refs"] = [ref for ref in document["refs"] if ref != key]
            if not document["refs"]:
                del document["refs"]
            refs = [ref for ref in current.refs if ref != key]
        stream = io.StringIO()
        _yaml().dump(document, stream)

        _verify_read_back(stream.getvalue(), current.model_copy(update={"refs": refs}))
        _atomic_write(path, stream.getvalue().encode("utf-8"))
    return file_version(path)


def add_evidence_to_file(path: Path, event_key: str, item: dict, *, expected_version: str) -> str:
    """Add one evidence entry (file, type, description) to an event in its topic file. Returns the new version."""
    with folder_lock(path.parent):
        _check_version(path, expected_version)
        text = path.read_text(encoding="utf-8")
        current = _read_model(text)
        event = next((known for known in current.events if known.key == event_key), None)
        if event is None:
            raise EventNotInFile(f"Event {event_key!r} steht nicht in {path.name}.")

        document = _yaml().load(text)
        entry = next(entry for entry in document["events"] if entry["key"] == event_key)
        entry.setdefault("evidence", []).append(item)
        stream = io.StringIO()
        _yaml().dump(document, stream)

        added = Evidence.model_validate(item)
        _verify_read_back(stream.getvalue(), current.model_copy(update={"events": [
            known.model_copy(update={"evidence": [*known.evidence, added]}) if known.key == event_key else known
            for known in current.events
        ]}))
        _atomic_write(path, stream.getvalue().encode("utf-8"))
    return file_version(path)


def remove_evidence_from_file(path: Path, event_key: str, file: str, *, expected_version: str) -> str:
    """Remove the evidence entry for one file from an event. The file on disk is not touched here."""
    with folder_lock(path.parent):
        _check_version(path, expected_version)
        text = path.read_text(encoding="utf-8")
        current = _read_model(text)
        event = next((known for known in current.events if known.key == event_key), None)
        if event is None or not any(item.file == file for item in event.evidence):
            raise StoreError(f"Der Beleg {file!r} steht nicht bei {event_key!r} in {path.name}.")

        document = _yaml().load(text)
        entry = next(entry for entry in document["events"] if entry["key"] == event_key)
        entry["evidence"] = [item for item in entry["evidence"] if item["file"] != file]
        if not entry["evidence"]:
            del entry["evidence"]
        stream = io.StringIO()
        _yaml().dump(document, stream)

        _verify_read_back(stream.getvalue(), current.model_copy(update={"events": [
            known.model_copy(update={"evidence": [item for item in known.evidence if item.file != file]})
            if known.key == event_key else known
            for known in current.events
        ]}))
        _atomic_write(path, stream.getvalue().encode("utf-8"))
    return file_version(path)


def restore_file(path: Path, data: bytes) -> None:
    """Put back the bytes a file had before a failed operation changed it. Atomic, like every write here."""
    _atomic_write(path, data)


def write_new_file(path: Path, data: bytes) -> None:
    """Write a binary file that must not exist yet (an attachment). Atomic, like the topic files."""
    if path.exists():
        raise StoreError(f"{path.name} existiert bereits.")
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, data)


_thread_locks: dict[str, threading.Lock] = {}
_thread_locks_guard = threading.Lock()
_held = threading.local()


def _thread_lock(key: str) -> threading.Lock:
    with _thread_locks_guard:
        return _thread_locks.setdefault(key, threading.Lock())


@contextmanager
def folder_lock(folder: Path) -> Iterator[None]:
    """One writer at a time for a data folder. The lock file is not a topic file, so the loader ignores it.

    Threads of this process take turns on a lock in memory, other processes on the file lock. A second flock
    in the same process would wait for the first one, so a folder this thread already holds is not locked again.
    """
    key = str(folder.resolve())
    if not hasattr(_held, "folders"):
        _held.folders = set()
    if key in _held.folders:
        yield
        return
    with _thread_lock(key), (folder / LOCK_FILE).open("a") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        _held.folders.add(key)
        try:
            yield
        finally:
            _held.folders.discard(key)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _yaml() -> YAML:
    """Round-trip YAML with the layout of the hand-written demo file (data/timeline.yaml), which it reproduces exactly."""
    yaml_rt = YAML(typ="rt")
    yaml_rt.preserve_quotes = True  # without it, every quoted value in a file loses its quotes on the next write
    yaml_rt.indent(mapping=2, sequence=4, offset=2)
    yaml_rt.width = 4096
    return yaml_rt


def _check_version(path: Path, expected_version: str | None) -> None:
    current = file_version(path) if path.exists() else None
    if current == expected_version:
        return
    if expected_version is None:
        raise VersionConflict(f"{path.name} existiert bereits.")
    raise VersionConflict(f"{path.name} wurde inzwischen geändert. Bitte neu laden, bevor du speicherst.")


def _read_model(text: str) -> Timeline:
    try:
        return Timeline.model_validate(yaml.load(text, Loader=_YAML_LOADER))
    except (yaml.YAMLError, ValueError) as exc:
        raise StoreError(f"Die Datei ist nach der Änderung nicht lesbar, deshalb wird nichts geschrieben: {exc}") from exc


def _verify_read_back(text: str, expected: Timeline) -> None:
    """The text must read back as exactly the timeline it should hold. Otherwise nothing is written."""
    if _read_model(text) != expected:
        raise StoreError("Die geänderte Datei würde sich beim Lesen anders verhalten als geplant, deshalb wird nichts geschrieben.")


def _set_changed_fields(item, old: Event, new: Event) -> None:
    """Write only the fields that differ. A field without a value is removed, so the file keeps only what is set."""
    if old.t0 != new.t0:
        item["t0"] = new.t0.isoformat()
    if old.phase != new.phase:
        item["phase"] = new.phase.value
    if old.actor != new.actor:
        item["actor"] = new.actor
    if old.system != new.system:
        _put(item, "system", new.system)
    if old.action != new.action:
        item["action"] = new.action
    if old.status != new.status:
        item["status"] = new.status.value
    if old.triggered_by != new.triggered_by:
        _put(item, "triggered_by", list(new.triggered_by) or None)
    if old.details != new.details:
        _put(item, "details", _text(new.details) or None)


def _put(item, field: str, value) -> None:
    if value is None:
        item.pop(field, None)
    else:
        item[field] = value


def _text(value: str):
    """Multi-line text is written as a literal block, so Markdown stays readable in the file."""
    return LiteralScalarString(value) if "\n" in value else value


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
        topic["summary"] = _text(timeline.topic.summary)
    document: dict = {"topic": topic}

    events = [_event_document(event) for event in timeline.events]
    if events:
        document["events"] = events
    if timeline.refs:
        document["refs"] = list(timeline.refs)
    return document


def _event_document(event: Event) -> dict:
    """One event as it is written in a topic file. Empty optional fields are left out."""
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
        item["details"] = _text(event.details)
    if event.evidence:
        item["evidence"] = [_evidence_document(evidence) for evidence in event.evidence]
    return item


def _evidence_document(evidence) -> dict:
    item: dict = {"file": evidence.file, "type": evidence.type}
    if evidence.description:
        item["description"] = evidence.description
    return item
