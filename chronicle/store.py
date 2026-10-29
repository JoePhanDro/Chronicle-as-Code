"""Writing topic files (ADR 0008, issues #89 and #97). The server is the only writer of its data folder.

A write is checked against the version the caller read, is atomic (temporary file, then rename), and is only
done when the new text reads back as the expected timeline. The version of a file is the SHA-256 of its bytes, so
an edit made in an editor or by `git pull` is noticed, and the caller gets a conflict instead of overwriting it.

Changes to an existing file keep what the file already has: comments, key order and layout. Only the fields that
changed are touched (ruamel.yaml round trip).
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import io
import os
import secrets
import stat
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import yaml
from pydantic import ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError as RuamelError
from ruamel.yaml.scalarstring import LiteralScalarString

from .loader import TimelineError, link_below, read_topic_text
from .models import Event, EventComment, Evidence, Timeline, Topic
from .schema import SCHEMA_URL
from .yamlscan import (
    MAX_YAML_BYTES,
    TOO_LARGE,
    YAML_LOADER,
    YamlRejected,
    describe_validation_error,
    describe_yaml_error,
    load_yaml,
    one_line,
    scan,
)

LOCK_FILE = ".chronicle.lock"
HEADER = f"# yaml-language-server: $schema={SCHEMA_URL}\n"  # only for new files; existing files keep their header


class StoreError(TimelineError):
    """The file cannot be written as requested."""


class VersionConflict(StoreError):
    """The file changed since the caller read it. Nothing was written."""


class EventNotInFile(StoreError):
    """The event is not defined in the file it was looked up in."""


def require_no_link(root: Path, path: Path) -> None:
    """Refuse a path below the data folder `root` that goes through a link. `root` itself may be one (a mount)."""
    link = link_below(root, path)
    if link is not None:
        raise StoreError(f"{link.relative_to(root).as_posix()} ist ein Link. Links sind im Datenordner nicht erlaubt.")


def file_version(path: Path) -> str:
    """The version of a topic file: the SHA-256 of its bytes. Read in pieces, so any size is hashed in constant memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _read_text(path: Path) -> str:
    """The text of a topic file for a change. A file over the size limit or without UTF-8 is a StoreError."""
    try:
        return read_topic_text(path)
    except TimelineError as exc:
        raise StoreError(str(exc)) from exc


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
        text = _read_text(path)
        start, end = _entry_span(text, key, path.name)
        entry = text[start:end]
        old = _entry_event(entry)

        # Only the entry of this event goes through the round trip. Parsing the whole file here took seconds for a
        # file of 10,000 events (issue #125); the rest of the file stays as it is, byte for byte.
        prefix = "events:\n  - "
        item = _load_document(prefix + entry)["events"][0]
        _set_changed_fields(item, old, event)
        stream = io.StringIO()
        _yaml().dump({"events": [item]}, stream)
        if not stream.getvalue().startswith(prefix):
            raise StoreError("Der geänderte Eintrag hat eine andere Form als erwartet, deshalb wird nichts geschrieben.")
        new_entry = stream.getvalue()[len(prefix):]
        _verify_read_back_entry(new_entry, event.model_copy(update={"evidence": old.evidence}))
        new_text = text[:start] + new_entry + text[end:]
        _check_size(new_text)
        try:
            scan(new_text)  # the entry was checked alone; the node limit is the file's
        except YamlRejected as exc:
            raise StoreError(f"Die Datei würde mit dieser Änderung die Grenzen überschreiten: {exc}. "
                             "Es wird nichts geschrieben.") from exc
        _atomic_write(path, new_text.encode("utf-8"))
    return file_version(path)


def _compose(text: str, name: str):
    try:
        scan(text)  # the file may come from Git, and the C composer crashes on deep input
        return yaml.compose(text, Loader=YAML_LOADER)
    except YamlRejected as exc:
        raise StoreError(f"{one_line(name)}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise StoreError(f"{one_line(name)}: {describe_yaml_error(exc)}") from exc


def _entry_span(text: str, key: str, name: str) -> tuple[int, int]:
    """Where the entry of one event sits in the file text: from its first key to the end of its last line.

    The YAML composer gives the positions, so the file is not loaded into objects to find the entry.
    """
    root = _compose(text, name)
    fields = root.value if isinstance(root, yaml.MappingNode) else []
    events = next((value for field, value in fields if field.value == "events"), None)
    for entry in events.value if events is not None else []:
        entry_key = next(value for field, value in entry.value if field.value == "key")
        if entry_key.value == key:
            return entry.start_mark.index, _line_end(text, _last_index(entry))
    raise EventNotInFile(f"Event {key!r} steht nicht in {name}.")


def _last_index(node) -> int:
    """The position after the last character of a YAML node."""
    if isinstance(node, yaml.MappingNode):
        return max(_last_index(value) for _, value in node.value)
    if isinstance(node, yaml.SequenceNode):
        return max(_last_index(value) for value in node.value)
    return node.end_mark.index


def _line_end(text: str, position: int) -> int:
    """The start of the line after position, or position itself if it is already at a line start."""
    if position == 0 or text[position - 1] == "\n":
        return position
    newline = text.find("\n", position)
    return len(text) if newline == -1 else newline + 1


def _entry_event(entry: str) -> Event:
    """The event of one entry text, validated with the same model as a whole topic file."""
    try:
        document = load_yaml("events:\n  - " + entry)
        return Timeline.model_validate({"topic": {"key": "entry", "title": "entry"}, "events": document["events"]}).events[0]
    except YamlRejected as exc:
        raise StoreError(f"Die Datei ist nach der Änderung nicht lesbar, deshalb wird nichts geschrieben: {exc}") from exc
    except ValidationError as exc:
        raise StoreError("Die Datei ist nach der Änderung nicht lesbar, deshalb wird nichts geschrieben: "
                         f"{describe_validation_error(exc)}") from exc


def _verify_read_back_entry(entry: str, expected: Event) -> None:
    if _entry_event(entry) != expected:
        raise StoreError("Der geänderte Eintrag würde sich beim Lesen anders verhalten als geplant, deshalb wird nichts geschrieben.")


def create_topic_file(path: Path, topic: Topic) -> str:
    """Create the file of a new topic, without events. Refused if the file exists. Returns its version."""
    return write_timeline(path, Timeline(topic=topic), expected_version=None)


def update_topic_in_file(path: Path, topic: Topic, *, expected_version: str) -> str:
    """Change the title, kind and summary of the topic a file defines. The key does not change. Returns the new version."""
    with folder_lock(path.parent):
        _check_version(path, expected_version)
        text = _read_text(path)
        current = _read_model(text)
        if current.topic.key != topic.key:
            raise StoreError(f"{path.name} gehört zum Topic {current.topic.key!r}, nicht zu {topic.key!r}.")

        document = _load_document(text)
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
        text = _read_text(path)
        current = _read_model(text)
        if any(known.key == event.key for known in current.events):
            raise StoreError(f"Event {event.key!r} steht bereits in {path.name}.")

        document = _load_document(text)
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
        text = _read_text(path)
        current = _read_model(text)
        if not any(known.key == key for known in current.events):
            raise EventNotInFile(f"Event {key!r} steht nicht in {path.name}.")

        document = _load_document(text)
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
        text = _read_text(path)
        current = _read_model(text)
        if present == (key in current.refs):
            return file_version(path)

        document = _load_document(text)
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
        text = _read_text(path)
        current = _read_model(text)
        event = next((known for known in current.events if known.key == event_key), None)
        if event is None:
            raise EventNotInFile(f"Event {event_key!r} steht nicht in {path.name}.")

        document = _load_document(text)
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


def add_comment_to_file(path: Path, event_key: str, comment: dict, *, expected_version: str) -> str:
    """Append one validated comment to an event in its topic file."""
    with folder_lock(path.parent):
        _check_version(path, expected_version)
        text = _read_text(path)
        current = _read_model(text)
        event = next((known for known in current.events if known.key == event_key), None)
        if event is None:
            raise EventNotInFile(f"Event {event_key!r} steht nicht in {path.name}.")
        document = _load_document(text)
        entry = next(entry for entry in document["events"] if entry["key"] == event_key)
        entry.setdefault("comments", []).append(comment)
        stream = io.StringIO()
        _yaml().dump(document, stream)
        added = EventComment.model_validate(comment)
        _verify_read_back(stream.getvalue(), current.model_copy(update={"events": [
            known.model_copy(update={"comments": [*known.comments, added]}) if known.key == event_key else known
            for known in current.events
        ]}))
        _atomic_write(path, stream.getvalue().encode("utf-8"))
    return file_version(path)


def _without_evidence(model, event_key: str, file: str):
    """The model with the evidence entry for `file` taken off the event."""
    def stripped(known):
        if known.key != event_key:
            return known
        return known.model_copy(update={"evidence": [item for item in known.evidence if item.file != file]})

    return model.model_copy(update={"events": [stripped(known) for known in model.events]})


def _drop_evidence_entry(document, event_key: str, file: str) -> None:
    entry = next(entry for entry in document["events"] if entry["key"] == event_key)
    entry["evidence"] = [item for item in entry["evidence"] if item["file"] != file]
    if not entry["evidence"]:
        del entry["evidence"]


def remove_evidence_from_file(path: Path, event_key: str, file: str, *, expected_version: str) -> str:
    """Remove the evidence entry for one file from an event. The file on disk is not touched here."""
    with folder_lock(path.parent):
        _check_version(path, expected_version)
        text = _read_text(path)
        current = _read_model(text)
        event = next((known for known in current.events if known.key == event_key), None)
        if event is None or not any(item.file == file for item in event.evidence):
            raise StoreError(f"Der Beleg {file!r} steht nicht bei {event_key!r} in {path.name}.")

        document = _load_document(text)
        _drop_evidence_entry(document, event_key, file)
        stream = io.StringIO()
        _yaml().dump(document, stream)

        _verify_read_back(stream.getvalue(), _without_evidence(current, event_key, file))
        _atomic_write(path, stream.getvalue().encode("utf-8"))
    return file_version(path)


def restore_file(path: Path, data: bytes) -> None:
    """Put back the bytes a file had before a failed operation changed it. Atomic, like every write here."""
    _atomic_write(path, data)


def write_new_file(path: Path, data: bytes, *, root: Path) -> None:
    """Write a binary file that must not exist yet (an attachment). Atomic, like the topic files.

    `root` is the data folder. No folder between it and the file may be a link, or the file would land outside.
    """
    require_no_link(root, path.parent)
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
    with _thread_lock(key), _open_lock_file(folder) as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        _held.folders.add(key)
        try:
            yield
        finally:
            _held.folders.discard(key)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _open_lock_file(folder: Path):
    """The lock file, created if missing. A link under that name is an error: it would lock or create its target.

    So is anything that is not a regular file (a folder, a pipe).
    """
    try:
        descriptor = os.open(folder / LOCK_FILE, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o666)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.EISDIR):
            raise StoreError(f"{LOCK_FILE} ist ein Link oder ein Ordner. Der Datenordner wird nicht beschrieben.") from exc
        raise
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise StoreError(f"{LOCK_FILE} ist keine gewöhnliche Datei. Der Datenordner wird nicht beschrieben.")
        return os.fdopen(descriptor, "r+b")
    except BaseException:
        os.close(descriptor)
        raise


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


def _load_document(text: str):
    """The round-trip document of a text that passed the scan. ruamel (YAML 1.2) can still refuse what libyaml read
    (a tab after a value, a Unicode line break): the message names the place, not the content."""
    try:
        return _yaml().load(text)
    except RuamelError as exc:
        raise StoreError(f"Die Datei lässt sich nicht bearbeiten: {describe_yaml_error(exc)}") from exc
    except RecursionError as exc:
        raise StoreError("Die Datei lässt sich nicht bearbeiten: zu tief verschachtelt") from exc


def _check_size(text: str) -> None:
    """A topic file over the limit could not be read back by the next load, so it is not written."""
    if len(text.encode("utf-8")) > MAX_YAML_BYTES:
        raise StoreError(f"Die Datei würde mit dieser Änderung zu groß: {TOO_LARGE}. Es wird nichts geschrieben.")


def _read_model(text: str) -> Timeline:
    try:
        return Timeline.model_validate(load_yaml(text))
    except YamlRejected as exc:
        raise StoreError(f"Die Datei ist nach der Änderung nicht lesbar, deshalb wird nichts geschrieben: {exc}") from exc
    except ValidationError as exc:
        raise StoreError("Die Datei ist nach der Änderung nicht lesbar, deshalb wird nichts geschrieben: "
                         f"{describe_validation_error(exc)}") from exc


def _verify_read_back(text: str, expected: Timeline) -> None:
    """The text must read back as exactly the timeline it should hold. Otherwise nothing is written."""
    _check_size(text)
    if _read_model(text) != expected:
        raise StoreError("Die geänderte Datei würde sich beim Lesen anders verhalten als geplant, deshalb wird nichts geschrieben.")


def _stored_values(event: Event) -> dict:
    """The value each field has in the file; None where the field is left out."""
    return {
        "t0": event.t0.isoformat(),
        "phase": event.phase.value,
        "actor": event.actor,
        "system": event.system,
        "action": event.action,
        "status": event.status.value,
        "triggered_by": list(event.triggered_by) or None,
        "techniques": list(event.techniques) or None,
        "tags": list(event.tags) or None,
        "comments": [_comment_document(comment) for comment in event.comments] or None,
        "details": _text(event.details) or None,
    }


def _set_changed_fields(item, old: Event, new: Event) -> None:
    """Write only the fields that differ. A field without a value is removed, so the file keeps only what is set."""
    for field, value in _stored_values(new).items():
        if getattr(old, field) != getattr(new, field):
            _put(item, field, value)


def _put(item, field: str, value) -> None:
    if value is None:
        item.pop(field, None)
    else:
        item[field] = value


def _text(value: str):
    """Multi-line text is written as a literal block, so Markdown stays readable in the file."""
    return LiteralScalarString(value) if "\n" in value else value


def _atomic_write(path: Path, data: bytes) -> None:
    """Write path atomically: a new temporary file next to it, then rename. Never follows a link.

    The temporary name is random and the file is created exclusively (O_EXCL, O_NOFOLLOW), so a link that
    sits in the folder under any name is not written through. A target that is a link is refused. The file keeps its
    mode; a new file gets the mode the umask gives it (the open call applies it).
    """
    try:
        existing = path.lstat()
    except FileNotFoundError:
        existing = None
    if existing is not None and stat.S_ISLNK(existing.st_mode):
        raise StoreError(f"{path.name} ist ein Link und wird nicht beschrieben.")

    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o666)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            if existing is not None:
                os.fchmod(handle.fileno(), stat.S_IMODE(existing.st_mode))
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


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
    if event.techniques:
        item["techniques"] = list(event.techniques)
    if event.tags:
        item["tags"] = list(event.tags)
    if event.comments:
        item["comments"] = [_comment_document(comment) for comment in event.comments]
    if event.details:
        item["details"] = _text(event.details)
    if event.evidence:
        item["evidence"] = [_evidence_document(evidence) for evidence in event.evidence]
    return item


def _comment_document(comment) -> dict:
    return {"created_at": comment.created_at.isoformat(), "author": comment.author, "text": _text(comment.text)}


def _evidence_document(evidence) -> dict:
    item: dict = {"file": evidence.file, "type": evidence.type}
    if evidence.description:
        item["description"] = evidence.description
    return item
