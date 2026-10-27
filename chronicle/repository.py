"""Topics and events: import and manual create/update/delete. Reads live in queries.py, errors in errors.py. All queries are parameterized."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import stat
from collections.abc import Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from . import accounts
from .check import load_named_folder
from .errors import Conflict, Invalid, NotFound, RepositoryError  # noqa: F401  (re-exported)
from .evidence import EVIDENCE_TYPES, MAX_DESCRIPTION_LENGTH, Blob
from .loader import TOPIC_FILE_PATTERNS, TimelineError, find_topic_files
from .models import Event, Timeline, Topic
from .queries import (  # noqa: F401  (re-exported: callers use repository.get_event and so on)
    EVENT_SELECT,
    EventFilter,
    count_events,
    evidence_meta,
    find_events,
    get_event,
    get_event_links,
    get_evidence_content,
    get_topic,
    list_all_topic_keys,
    list_events,
    list_topic_events,
    list_topics,
    most_pressing_status,
    search_actors,
    search_systems,
    topic_keys_of,
    triggers_from_db,
    with_evidence,
)
from .store import (
    EventNotInFile,
    StoreError,
    VersionConflict,
    add_event_to_file,
    add_evidence_to_file,
    create_topic_file,
    file_version,
    folder_lock,
    remove_event_from_file,
    remove_evidence_from_file,
    restore_file,
    set_ref_in_file,
    update_event_in_file,
    update_topic_in_file,
    require_no_link,
    write_new_file,
)

EDITABLE_FIELDS = ("t0", "phase", "actor", "system", "action", "status", "triggered_by", "details")


@dataclass
class ImportReport:
    topic: str
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (key, reason)


def _triggers_to_db(keys: Sequence[str]) -> str | None:
    return json.dumps(list(keys)) if keys else None


def _event_values(event: Event) -> tuple:
    return (
        event.t0.isoformat(),
        event.phase.value,
        event.actor,
        event.system,
        event.action,
        event.status.value,
        _triggers_to_db(event.triggered_by),
        event.details,
    )


def _event_pk(conn: sqlite3.Connection, key: str) -> int | None:
    row = conn.execute("SELECT pk FROM events WHERE key = ?", (key,)).fetchone()
    return row["pk"] if row else None


def _replace_import_evidence(
    conn: sqlite3.Connection, event_pk: int, event: Event, blobs: Mapping[str, Blob], keep_content: bool = True
) -> bool:
    """Replace the evidence of an imported event. Each file must have been read and checked by the caller.

    Returns True if the evidence differs from what the database held, so that an unchanged re-import changes nothing.
    """
    before = sorted(
        (row["file"], row["type"], row["description"], row["sha256"])
        for row in conn.execute("SELECT file, type, description, sha256 FROM evidence WHERE event_pk = ?", (event_pk,))
    )
    conn.execute("DELETE FROM evidence WHERE event_pk = ?", (event_pk,))
    for item in event.evidence:
        blob = blobs.get(item.file)
        if blob is None:
            raise ValueError(f"Evidence-Datei fehlt für Event {event.key!r}: {item.file!r}")
        conn.execute(
            """
            INSERT INTO evidence (event_pk, file, type, description, mime, size, sha256, content)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            # With keep_content=False the file in the data folder is the only copy (issue #89): the index keeps the
            # checksum, and readers take the bytes from the file.
            (event_pk, item.file, item.type, item.description, blob.mime, blob.size, blob.sha256,
             blob.content if keep_content else None),
        )
    after = sorted((item.file, item.type, item.description, blobs[item.file].sha256) for item in event.evidence)
    return before != after


def _mark_manual(conn: sqlite3.Connection, event_pk: int) -> None:
    """Changes in the UI or API keep the event out of later imports (ADR 0003)."""
    conn.execute(
        "UPDATE events SET source = 'manual', updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE pk = ?",
        (event_pk,),
    )


def _check_triggers(conn: sqlite3.Connection, triggered_by: Sequence[str], own_key: str | None = None) -> None:
    """Every cause must exist and must not be the event itself. With own_key, cycles are refused too (docs/notation.md, 5c)."""
    for cause in triggered_by:
        if cause == own_key:
            raise Invalid("Ein Event kann sich nicht selbst auslösen")
        if _event_pk(conn, cause) is None:
            raise Invalid(f"triggered_by {cause!r} ist kein bekannter Event-Key")
    if own_key is not None and _closes_cycle(conn, own_key, triggered_by):
        raise Invalid(f"Auslösungen dürfen keinen Kreis bilden (über {own_key!r})")


def _closes_cycle(conn: sqlite3.Connection, key: str, causes: Sequence[str]) -> bool:
    """True if following the triggers from `causes` leads back to `key`. Walks the stored triggers."""
    pending = list(causes)
    seen: set[str] = set()
    while pending:
        node = pending.pop()
        if node == key:
            return True
        if node in seen:
            continue
        seen.add(node)
        row = conn.execute("SELECT triggered_by FROM events WHERE key = ?", (node,)).fetchone()
        if row is not None:
            pending.extend(triggers_from_db(row["triggered_by"]))
    return False


def _topic_pk(conn: sqlite3.Connection, key: str) -> int:
    row = conn.execute("SELECT pk FROM topics WHERE key = ?", (key,)).fetchone()
    if row is None:
        raise NotFound(f"Topic {key!r} nicht gefunden")
    return row["pk"]


# Import

def import_timeline(conn: sqlite3.Connection, timeline: Timeline, blobs: Mapping[str, Blob] | None = None) -> ImportReport:
    """Import one topic file. See import_timelines."""
    return import_timelines(conn, [(timeline, blobs or {})])[0]


def import_timelines(
    conn: sqlite3.Connection, items: Sequence[tuple[Timeline, Mapping[str, Blob]]], *, keep_content: bool = True
) -> list[ImportReport]:
    """Create or update topics and their events from topic files, in one transaction.

    `blobs` of each file maps its evidence paths to the checked content (see evidence.check_file).
    Events may refer to each other across files, so all files are written before the references are checked.
    Idempotent: the same files again change nothing. Events edited in the UI (source 'manual') and
    events deleted in the UI (tombstones) are skipped and reported. Links are only added, never removed.
    Any failed check rolls back everything, including the evidence contents.
    """
    owners: dict[str, str] = {}
    for timeline, _ in items:
        for event in timeline.events:
            if event.key in owners:
                raise ValueError(
                    f"Event {event.key!r} ist in den Topics {owners[event.key]!r} und {timeline.topic.key!r} definiert"
                )
            owners[event.key] = timeline.topic.key

    with conn:
        reports = [_write_topic(conn, timeline, blobs, keep_content) for timeline, blobs in items]

        for timeline, _ in items:
            for event in timeline.events:
                _check_triggers(conn, event.triggered_by, own_key=event.key)
            for key in timeline.refs:
                if _event_pk(conn, key) is None:
                    raise ValueError(f"refs: Event {key!r} existiert nicht (in keiner Datei definiert)")

        for (timeline, _), report in zip(items, reports):
            _link_topic(conn, timeline, report)
    return reports


def reindex(conn: sqlite3.Connection, data_dir: Path, known_keys: Collection[str] | None = None) -> list[ImportReport]:
    """Make the database match the topic files in data_dir (ADR 0008). Returns the import reports.

    The files are the truth, so events are taken from them even if they were last edited in the app. Also records
    the version of each file and the file each event is defined in, which the next edit checks against.
    Nothing is written if any file fails the checks.
    """
    if known_keys is None:
        known_keys = {row["key"] for row in conn.execute("SELECT key FROM events")}
    named = load_named_folder(data_dir, known_keys=known_keys)
    indexed = {row["event_key"] for row in conn.execute("SELECT event_key FROM event_files")}
    with conn:
        # In file mode the files win over earlier manual edits, so no event is kept back as 'manual' (ADR 0003).
        conn.executemany(
            "UPDATE events SET source = 'import' WHERE key = ?",
            [(event.key,) for _, timeline, _ in named for event in timeline.events],
        )
    reports = write_index(conn, data_dir, named, keep_content=False)
    defined = {event.key for _, timeline, _ in named for event in timeline.events}
    wanted = {(timeline.topic.key, key) for _, timeline, _ in named
              for key in [*(event.key for event in timeline.events), *timeline.refs]}
    with conn:
        # An event that was in a file at the last index and is in none now was removed from its file. The index
        # follows. Events that were never in a file (created in the database) are not touched.
        for key in indexed - defined:
            conn.execute("DELETE FROM events WHERE key = ?", (key,))
        # Links are the files' truth too: a link that is no longer under refs (or on the event) is removed. Import
        # alone only adds links (ADR 0003), so this is where removals happen.
        stale = [row for row in conn.execute(
            "SELECT topics.key AS topic, events.key AS event FROM topic_events "
            "JOIN topics ON topics.pk = topic_events.topic_pk JOIN events ON events.pk = topic_events.event_pk"
        ).fetchall() if row["event"] in defined and (row["topic"], row["event"]) not in wanted]
        for row in stale:
            conn.execute(
                "DELETE FROM topic_events WHERE topic_pk = (SELECT pk FROM topics WHERE key = ?) "
                "AND event_pk = (SELECT pk FROM events WHERE key = ?)",
                (row["topic"], row["event"]),
            )
    return reports


def write_index(conn: sqlite3.Connection, data_dir: Path, named: list, *, keep_content: bool = True) -> list[ImportReport]:
    """Write checked folder contents (from load_named_folder) into the index. Events edited in the app stay
    'manual' and are kept back, as before (ADR 0003). See reindex for the file mode."""
    items = [(timeline, blobs) for _, timeline, blobs in named]
    reports = import_timelines(conn, items, keep_content=keep_content)
    with conn:
        conn.execute("DELETE FROM source_files")
        conn.execute("DELETE FROM event_files")
        conn.execute("DELETE FROM topic_files")
        for name, timeline, _ in named:
            conn.execute("INSERT INTO source_files (path, sha256) VALUES (?, ?)", (name, file_version(data_dir / name)))
            conn.execute("INSERT INTO topic_files (topic_key, path) VALUES (?, ?)", (timeline.topic.key, name))
            conn.executemany(
                "INSERT INTO event_files (event_key, path) VALUES (?, ?)",
                [(event.key, name) for event in timeline.events],
            )
    return reports


def _linked_elsewhere(conn: sqlite3.Connection, key: str) -> list[str]:
    """The topics an event is linked to besides its home topic, i.e. where it appears under refs."""
    return [item["key"] for item in conn.execute(
        "SELECT topics.key FROM topic_events JOIN topics ON topics.pk = topic_events.topic_pk "
        "JOIN events ON events.pk = topic_events.event_pk WHERE events.key = ? AND topics.key != events.home_topic "
        "ORDER BY topics.key",
        (key,),
    ).fetchall()]


def _change_link(conn: sqlite3.Connection, key: str, topic_key: str, *, present: bool, data_dir: Path) -> None:
    located = conn.execute("SELECT path FROM topic_files WHERE topic_key = ?", (topic_key,)).fetchone()
    if located is None:
        raise NotFound(f"Topic {topic_key!r} steht in keiner Datei des Datenordners. Bitte chronicle apply ausführen.")
    try:
        set_ref_in_file(data_dir / located["path"], key, present=present,
                        expected_version=_recorded_version(conn, located["path"]))
    except VersionConflict as exc:
        raise Conflict(f"{located['path']} wurde außerhalb von Chronicle geändert. Bitte chronicle apply ausführen. "
                       f"Der Verweis wurde nicht geändert ({exc}).") from exc
    except StoreError as exc:
        raise Invalid(str(exc)) from exc


def _change_links(conn: sqlite3.Connection, key: str, *, add, remove, data_dir: Path) -> None:
    """Link an event to topics (add) and unlink it from topics (remove). Each change is one topic file."""
    for topic_key in sorted(add):
        _change_link(conn, key, topic_key, present=True, data_dir=data_dir)
    for topic_key in sorted(remove):
        _change_link(conn, key, topic_key, present=False, data_dir=data_dir)


def _update_in_folder(
    conn: sqlite3.Connection, key: str, event: Event, topic_keys: list[str], data_dir: Path,
    actor_id: int, ip: str, changed: list[str],
) -> dict:
    """The event's fields go to its home file, the links to the files of the other topics. Then the index follows."""
    with _all_or_nothing(data_dir):
        written = None
        if [name for name in changed if name != "topics"]:
            located = conn.execute("SELECT path FROM event_files WHERE event_key = ?", (key,)).fetchone()
            if located is None:
                raise Invalid(f"Event {key!r} steht in keiner Datei des Datenordners. Bitte chronicle apply ausführen.")
            written = located["path"]
            try:
                update_event_in_file(data_dir / located["path"], key, event,
                                     expected_version=_recorded_version(conn, located["path"]))
            except VersionConflict as exc:
                raise Conflict(
                    f"{located['path']} wurde außerhalb von Chronicle geändert. Bitte chronicle apply ausführen. "
                    f"Die Änderung wurde nicht gespeichert ({exc})."
                ) from exc
            except (EventNotInFile, StoreError) as exc:
                raise Invalid(str(exc)) from exc
        if "topics" in changed:
            home = conn.execute("SELECT home_topic FROM events WHERE key = ?", (key,)).fetchone()["home_topic"]
            linked = set(_linked_elsewhere(conn, key))
            wanted = set(topic_keys) - {home}
            _change_links(conn, key, add=wanted - linked, remove=linked - wanted, data_dir=data_dir)
            reindex(conn, data_dir)
        else:
            _index_field_change(conn, key, event, written, data_dir)
    accounts.audit(conn, "event_updated", user_id=actor_id, target=key, detail=",".join(changed), ip=ip)
    return get_event(conn, key)


def _index_field_change(conn: sqlite3.Connection, key: str, event: Event, written: str, data_dir: Path) -> None:
    """The index after the fields of one event were written to its file (issue #125).

    Only that file changed. If every other file is exactly as the index recorded it, the event's row and the file's
    version are updated, and no file is read again. Otherwise the folder is indexed as a whole, which checks every file.
    """
    if not _folder_as_indexed(conn, data_dir, changed=written):
        reindex(conn, data_dir)
        return
    with conn:
        conn.execute(
            """
            UPDATE events SET t0 = ?, phase = ?, actor = ?, system = ?, action = ?, status = ?,
                              triggered_by = ?, details = ?, source = 'import', version = version + 1,
                              updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
            WHERE key = ?
            """,
            (*_event_values(event), key),
        )
        conn.execute("UPDATE source_files SET sha256 = ? WHERE path = ?", (file_version(data_dir / written), written))
        # A full index holds no attachment bytes (keep_content=False, issue #89); the files are the only copy.
        conn.execute("UPDATE evidence SET content = NULL WHERE content IS NOT NULL")


def _folder_as_indexed(conn: sqlite3.Connection, data_dir: Path, *, changed: str) -> bool:
    """True if the topic files on disk are the ones the index recorded, except the file named changed (its new
    version is recorded by the caller). A file that is new, gone or edited outside Chronicle makes this False."""
    recorded = {row["path"]: row["sha256"] for row in conn.execute("SELECT path, sha256 FROM source_files")}
    on_disk = {path.relative_to(data_dir).as_posix(): path for path in find_topic_files(data_dir)}
    if recorded.keys() != on_disk.keys() or changed not in recorded:
        return False
    return all(name == changed or file_version(path) == recorded[name] for name, path in on_disk.items())


def _write_topic(
    conn: sqlite3.Connection, timeline: Timeline, blobs: Mapping[str, Blob], keep_content: bool = True
) -> ImportReport:
    topic = timeline.topic
    report = ImportReport(topic=topic.key)

    for event in timeline.events:
        row = conn.execute("SELECT home_topic, source FROM events WHERE key = ?", (event.key,)).fetchone()
        if row is not None and row["home_topic"] != topic.key:
            raise ValueError(
                f"Event {event.key!r} ist im Topic {row['home_topic']!r} definiert. "
                f"In {topic.key!r} bitte per refs referenzieren."
            )
        if row is not None and row["source"] == "manual":
            report.skipped.append((event.key, "in der Oberfläche bearbeitet"))

    conn.execute(
        """
        INSERT INTO topics (key, title, kind, summary, updated_at)
        VALUES (?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        ON CONFLICT (key) DO UPDATE SET
            title = excluded.title,
            kind = excluded.kind,
            summary = excluded.summary,
            updated_at = excluded.updated_at
        """,
        (topic.key, topic.title, topic.kind, topic.summary),
    )

    skipped = {key for key, _ in report.skipped}
    for event in timeline.events:
        if event.key in skipped:
            continue
        existed = _event_pk(conn, event.key) is not None
        # An existing event is only touched when a field differs, so an unchanged re-import keeps the version
        # (a bump would make an open editor fail with 409).
        cursor = conn.execute(
            """
            INSERT INTO events
                (key, t0, phase, actor, system, action, status, triggered_by, details,
                 home_topic, source, version, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'import', 1, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            ON CONFLICT (key) DO UPDATE SET
                t0 = excluded.t0,
                phase = excluded.phase,
                actor = excluded.actor,
                system = excluded.system,
                action = excluded.action,
                status = excluded.status,
                triggered_by = excluded.triggered_by,
                details = excluded.details,
                version = events.version + 1,
                updated_at = excluded.updated_at
            WHERE events.t0 IS NOT excluded.t0
               OR events.phase IS NOT excluded.phase
               OR events.actor IS NOT excluded.actor
               OR events.system IS NOT excluded.system
               OR events.action IS NOT excluded.action
               OR events.status IS NOT excluded.status
               OR events.triggered_by IS NOT excluded.triggered_by
               OR events.details IS NOT excluded.details
            """,
            (event.key, *_event_values(event), topic.key),
        )
        event_pk = _event_pk(conn, event.key)
        evidence_changed = _replace_import_evidence(conn, event_pk, event, blobs, keep_content)
        if existed and evidence_changed and cursor.rowcount == 0:
            # Evidence counts as an edit of the event (README, uploads), so it bumps the version too.
            conn.execute(
                "UPDATE events SET version = version + 1, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE pk = ?",
                (event_pk,),
            )
        if not existed:
            report.created.append(event.key)
        elif cursor.rowcount or evidence_changed:
            report.updated.append(event.key)
    return report


def _link_topic(conn: sqlite3.Connection, timeline: Timeline, report: ImportReport) -> None:
    skipped = {key for key, _ in report.skipped}
    topic_pk = _topic_pk(conn, timeline.topic.key)
    for key in [event.key for event in timeline.events] + list(timeline.refs):
        if key in skipped:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO topic_events (topic_pk, event_pk) SELECT ?, pk FROM events WHERE key = ?",
            (topic_pk, key),
        )


# Manual changes from the UI or the API

def _recorded_version(conn: sqlite3.Connection, path: str) -> str:
    row = conn.execute("SELECT sha256 FROM source_files WHERE path = ?", (path,)).fetchone()
    if row is None:
        raise Conflict(f"{path} ist im Index nicht bekannt. Bitte chronicle apply ausführen.")
    return row["sha256"]


@contextmanager
def _all_or_nothing(data_dir: Path) -> Iterator[list[Path]]:
    """Run a change of the data folder as one step: the files are written, the index follows (ADR 0008).

    If anything fails, the topic files go back to how they were read and the files the block created are removed.
    The yielded list collects those created files. An index that refuses the folder is reported as Invalid.
    The folder stays locked for the whole block: a change that runs meanwhile would otherwise be taken back by the
    rollback of this one (issue #126).
    """
    with folder_lock(data_dir):
        before = {path: path.read_bytes() for pattern in TOPIC_FILE_PATTERNS for path in data_dir.rglob(pattern)}
        created: list[Path] = []
        try:
            yield created
        except StoreError as exc:
            _restore_folder(data_dir, before, created)
            raise Invalid(str(exc)) from exc
        except TimelineError as exc:
            _restore_folder(data_dir, before, created)
            raise Invalid(f"Nichts wurde geändert, weil der Datenordner nicht gültig ist:\n{exc}") from exc
        except BaseException:
            _restore_folder(data_dir, before, created)
            raise


def _restore_folder(data_dir: Path, before: Mapping[Path, bytes], created: Sequence[Path]) -> None:
    for path in created:
        path.unlink(missing_ok=True)
    for pattern in TOPIC_FILE_PATTERNS:
        for path in data_dir.rglob(pattern):
            if path not in before:
                path.unlink(missing_ok=True)
    for path, data in before.items():
        if not path.exists() or path.read_bytes() != data:
            restore_file(path, data)


def _create_in_folder(
    conn: sqlite3.Connection, event: Event, topic_keys: list[str], data_dir: Path, actor_id: int, ip: str
) -> dict:
    if _event_pk(conn, event.key) is not None:
        raise Conflict(f"Event-Key {event.key!r} ist bereits vergeben")
    _check_triggers(conn, event.triggered_by, own_key=event.key)
    located = conn.execute("SELECT path FROM topic_files WHERE topic_key = ?", (topic_keys[0],)).fetchone()
    if located is None:
        raise NotFound(f"Topic {topic_keys[0]!r} steht in keiner Datei des Datenordners. Bitte chronicle apply ausführen.")
    # The event and its links are one change: if a link or the index fails, nothing of it stays in the files.
    with _all_or_nothing(data_dir):
        try:
            add_event_to_file(data_dir / located["path"], event,
                              expected_version=_recorded_version(conn, located["path"]))
        except VersionConflict as exc:
            raise Conflict(f"{located['path']} wurde außerhalb von Chronicle geändert. Bitte chronicle apply ausführen. "
                           f"Das Event wurde nicht angelegt ({exc}).") from exc
        except StoreError as exc:
            raise Invalid(str(exc)) from exc
        _change_links(conn, event.key, add=set(topic_keys[1:]), remove=set(), data_dir=data_dir)
        reindex(conn, data_dir)
    accounts.audit(conn, "event_created", user_id=actor_id, target=event.key, detail=f"topics={','.join(topic_keys)}", ip=ip)
    return get_event(conn, event.key)


def _delete_in_folder(conn: sqlite3.Connection, key: str, data_dir: Path, actor_id: int, ip: str) -> None:
    located = conn.execute("SELECT path FROM event_files WHERE event_key = ?", (key,)).fetchone()
    if located is None:
        raise Invalid(f"Event {key!r} steht in keiner Datei des Datenordners. Bitte chronicle apply ausführen.")
    with _all_or_nothing(data_dir):
        # The links go first: an event without links is still valid, a link to a missing event is not.
        _change_links(conn, key, add=set(), remove=set(_linked_elsewhere(conn, key)), data_dir=data_dir)
        try:
            remove_event_from_file(data_dir / located["path"], key,
                                   expected_version=_recorded_version(conn, located["path"]))
        except VersionConflict as exc:
            raise Conflict(f"{located['path']} wurde außerhalb von Chronicle geändert. Bitte chronicle apply ausführen. "
                           f"Nichts wurde gelöscht ({exc}).") from exc
        except (EventNotInFile, StoreError) as exc:
            raise Invalid(str(exc)) from exc
        reindex(conn, data_dir)
    accounts.audit(conn, "event_deleted", user_id=actor_id, target=key, ip=ip)


def create_event(
    conn: sqlite3.Connection, event: Event, topic_keys: list[str], actor_id: int, ip: str, data_dir: Path | None = None
) -> dict:
    """Create an event in its home topic. With data_dir the event is appended to the topic file first (ADR 0008)."""
    if data_dir is not None:
        return _create_in_folder(conn, event, topic_keys, data_dir, actor_id, ip)
    home = topic_keys[0]
    with conn:
        for key in topic_keys:
            _topic_pk(conn, key)
        if _event_pk(conn, event.key) is not None:
            raise Conflict(f"Event-Key {event.key!r} ist bereits vergeben")
        _check_triggers(conn, event.triggered_by, own_key=event.key)

        cursor = conn.execute(
            """
            INSERT INTO events (key, t0, phase, actor, system, action, status, triggered_by, details,
                                home_topic, source, version, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual', 1, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            """,
            (event.key, *_event_values(event), home),
        )
        _set_links(conn, cursor.lastrowid, topic_keys)

    accounts.audit(conn, "event_created", user_id=actor_id, target=event.key, detail=f"topics={','.join(topic_keys)}", ip=ip)
    return get_event(conn, event.key)


def update_event(
    conn: sqlite3.Connection,
    key: str,
    event: Event,
    topic_keys: list[str],
    expected_version: int,
    actor_id: int,
    ip: str,
    data_dir: Path | None = None,
) -> dict:
    """Change an event. With data_dir the change is written to the event's topic file first, and the index is
    then rebuilt from the folder (ADR 0008). Without it, only the database changes, as before."""
    row = conn.execute(
        "SELECT pk, version, home_topic FROM events WHERE key = ?", (key,)
    ).fetchone()
    if row is None:
        raise NotFound(f"Event {key!r} nicht gefunden")
    if row["version"] != expected_version:
        raise Conflict(f"Das Event wurde inzwischen geändert (aktuelle Version {row['version']}). Bitte neu laden.")
    if row["home_topic"] not in topic_keys:
        raise Invalid(f"Das Heimat-Topic {row['home_topic']!r} muss verknüpft bleiben")
    _check_triggers(conn, event.triggered_by, own_key=key)

    old = get_event(conn, key)
    old_topics = topic_keys_of(conn, row["pk"])
    new_values = {"t0": event.t0.isoformat(), "phase": event.phase.value, "actor": event.actor,
                  "system": event.system, "action": event.action, "status": event.status.value,
                  "triggered_by": event.triggered_by, "details": event.details}
    changed = [name for name in EDITABLE_FIELDS if str(old[name]) != str(new_values[name])]
    if sorted(old_topics) != sorted(topic_keys):
        changed.append("topics")
    if not changed:
        return old  # saving without changes creates no new revision
    if data_dir is not None:
        return _update_in_folder(conn, key, event, topic_keys, data_dir, actor_id, ip, changed)

    with conn:
        for topic_key in topic_keys:
            _topic_pk(conn, topic_key)
        conn.execute(
            """
            UPDATE events SET t0 = ?, phase = ?, actor = ?, system = ?, action = ?, status = ?,
                              triggered_by = ?, details = ?, source = 'manual', version = version + 1,
                              updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
            WHERE pk = ?
            """,
            (*_event_values(event), row["pk"]),
        )
        _set_links(conn, row["pk"], topic_keys)

    if changed:
        accounts.audit(conn, "event_updated", user_id=actor_id, target=key, detail=",".join(changed), ip=ip)
    return get_event(conn, key)


def _free_evidence_name(data_dir: Path, key: str, name: str, sha256: str) -> tuple[str, bool]:
    """A path under evidence/<event key>/ for an attachment, and whether the file still has to be written.

    The name is cleaned to letters, digits, dots, dashes and underscores. An identical file already there is reused;
    a different one with the same name gets a number, so nothing is overwritten.
    """
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "datei"
    stem, dot, extension = safe.rpartition(".") if "." in safe else (safe, "", "")
    try:
        require_no_link(data_dir, data_dir / "evidence" / key)
    except StoreError as exc:
        raise Invalid(str(exc)) from exc
    for number in range(1, 1000):
        candidate = Path("evidence") / key / (safe if number == 1 else f"{stem}-{number}{dot}{extension}")
        target = data_dir / candidate
        try:
            found = target.lstat()
        except FileNotFoundError:
            return candidate.as_posix(), True
        if not stat.S_ISREG(found.st_mode):
            raise Invalid(f"{candidate.as_posix()} ist ein Link oder keine gewöhnliche Datei.")
        if hashlib.sha256(target.read_bytes()).hexdigest() == sha256:
            return candidate.as_posix(), False
    raise Invalid("Zu viele Dateien mit demselben Namen bei diesem Event.")


def _add_evidence_in_folder(
    conn: sqlite3.Connection, key: str, blob: Blob, evidence_type: str, description: str, data_dir: Path,
    actor_id: int, ip: str,
) -> dict:
    if evidence_type not in EVIDENCE_TYPES:
        raise Invalid(f"Unbekannter Evidence-Typ {evidence_type!r}")
    if len(description) > MAX_DESCRIPTION_LENGTH:
        raise Invalid(f"Die Beschreibung darf höchstens {MAX_DESCRIPTION_LENGTH} Zeichen lang sein")
    located = conn.execute("SELECT path FROM event_files WHERE event_key = ?", (key,)).fetchone()
    if located is None:
        raise NotFound(f"Event {key!r} nicht gefunden")
    relative, must_write = _free_evidence_name(data_dir, key, blob.name, blob.sha256)
    if conn.execute(
        "SELECT 1 FROM evidence JOIN events ON events.pk = evidence.event_pk WHERE events.key = ? AND evidence.file = ?",
        (key, relative),
    ).fetchone():
        raise Invalid("Dieser Beleg ist bei dem Event bereits angehängt.")

    # The file goes to disk first and the topic file names it. If the topic file refuses, the file goes again.
    target = data_dir / relative
    item = {"file": relative, "type": evidence_type, "description": description.strip()}
    with _all_or_nothing(data_dir) as created:
        if must_write:
            write_new_file(target, blob.content, root=data_dir)
            created.append(target)
        try:
            add_evidence_to_file(data_dir / located["path"], key, item,
                                 expected_version=_recorded_version(conn, located["path"]))
        except VersionConflict as exc:
            raise Conflict(f"{located['path']} wurde außerhalb von Chronicle geändert. Bitte chronicle apply ausführen. "
                           f"Der Beleg wurde nicht angehängt ({exc}).") from exc
        except StoreError as exc:
            raise Invalid(str(exc)) from exc
        reindex(conn, data_dir)
    accounts.audit(conn, "evidence_added", user_id=actor_id, target=key, detail=relative, ip=ip)
    row = conn.execute(
        "SELECT evidence.pk FROM evidence JOIN events ON events.pk = evidence.event_pk "
        "WHERE events.key = ? AND evidence.file = ?",
        (key, relative),
    ).fetchone()
    return evidence_meta(conn, row["pk"])


def _delete_evidence_in_folder(conn: sqlite3.Connection, evidence_id: int, data_dir: Path, actor_id: int, ip: str) -> None:
    row = conn.execute(
        "SELECT evidence.file, events.key FROM evidence JOIN events ON events.pk = evidence.event_pk WHERE evidence.pk = ?",
        (evidence_id,),
    ).fetchone()
    if row is None:
        raise NotFound("Evidence nicht gefunden")
    located = conn.execute("SELECT path FROM event_files WHERE event_key = ?", (row["key"],)).fetchone()
    if located is None:
        raise Invalid(f"Event {row['key']!r} steht in keiner Datei des Datenordners. Bitte chronicle apply ausführen.")
    try:
        require_no_link(data_dir, (data_dir / row["file"]).parent)
    except StoreError as exc:
        raise Invalid(str(exc)) from exc
    with _all_or_nothing(data_dir):
        try:
            remove_evidence_from_file(data_dir / located["path"], row["key"], row["file"],
                                      expected_version=_recorded_version(conn, located["path"]))
        except VersionConflict as exc:
            raise Conflict(f"{located['path']} wurde außerhalb von Chronicle geändert. Bitte chronicle apply ausführen. "
                           f"Der Beleg wurde nicht entfernt ({exc}).") from exc
        except StoreError as exc:
            raise Invalid(str(exc)) from exc
        reindex(conn, data_dir)
    # The file goes last: until the index has accepted the removal, the topic file still names it.
    (data_dir / row["file"]).unlink(missing_ok=True)
    accounts.audit(conn, "evidence_deleted", user_id=actor_id, target=row["key"], detail=row["file"], ip=ip)


def add_evidence(
    conn: sqlite3.Connection, key: str, blob: Blob, evidence_type: str, description: str, actor_id: int, ip: str,
    data_dir: Path | None = None,
) -> dict:
    """Attach a checked file to an event. With data_dir the file goes to evidence/ and the topic file names it."""
    if data_dir is not None:
        return _add_evidence_in_folder(conn, key, blob, evidence_type, description, data_dir, actor_id, ip)
    if evidence_type not in EVIDENCE_TYPES:
        raise Invalid(f"Unbekannter Evidence-Typ {evidence_type!r}")
    if len(description) > MAX_DESCRIPTION_LENGTH:
        raise Invalid(f"Die Beschreibung darf höchstens {MAX_DESCRIPTION_LENGTH} Zeichen lang sein")

    with conn:
        event_pk = _event_pk(conn, key)
        if event_pk is None:
            raise NotFound(f"Event {key!r} nicht gefunden")
        cursor = conn.execute(
            """
            INSERT INTO evidence (event_pk, file, type, description, mime, size, sha256, content)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (event_pk, blob.name, evidence_type, description.strip(), blob.mime, blob.size, blob.sha256, blob.content),
        )
        _mark_manual(conn, event_pk)

    accounts.audit(conn, "evidence_added", user_id=actor_id, target=key, detail=blob.name, ip=ip)
    return evidence_meta(conn, cursor.lastrowid)


def delete_evidence(conn: sqlite3.Connection, evidence_id: int, actor_id: int, ip: str, data_dir: Path | None = None) -> None:
    if data_dir is not None:
        return _delete_evidence_in_folder(conn, evidence_id, data_dir, actor_id, ip)
    row = conn.execute(
        """
        SELECT evidence.event_pk, evidence.file, events.key
        FROM evidence JOIN events ON events.pk = evidence.event_pk
        WHERE evidence.pk = ?
        """,
        (evidence_id,),
    ).fetchone()
    if row is None:
        raise NotFound("Evidence nicht gefunden")

    with conn:
        conn.execute("DELETE FROM evidence WHERE pk = ?", (evidence_id,))
        _mark_manual(conn, row["event_pk"])

    accounts.audit(conn, "evidence_deleted", user_id=actor_id, target=row["key"], detail=row["file"], ip=ip)


def delete_event(conn: sqlite3.Connection, key: str, actor_id: int, ip: str, data_dir: Path | None = None) -> None:
    """Delete an event. With data_dir it is removed from its topic file first: the file is the record (ADR 0008),
    and Git has the earlier version. Without it only the database changes."""
    row = conn.execute("SELECT pk FROM events WHERE key = ?", (key,)).fetchone()
    if row is None:
        raise NotFound(f"Event {key!r} nicht gefunden")
    dependents = [item["key"] for item in conn.execute(
        "SELECT events.key FROM events, json_each(events.triggered_by) WHERE json_each.value = ? ORDER BY events.key",
        (key,),
    ).fetchall()]
    if dependents:
        raise Conflict(f"Event wird von {', '.join(dependents)} ausgelöst. Zuerst dort den Verweis entfernen.")
    if data_dir is not None:
        return _delete_in_folder(conn, key, data_dir, actor_id, ip)

    with conn:
        conn.execute("DELETE FROM events WHERE pk = ?", (row["pk"],))  # links and evidence cascade
    accounts.audit(conn, "event_deleted", user_id=actor_id, target=key, ip=ip)


def _set_links(conn: sqlite3.Connection, event_pk: int, topic_keys: list[str]) -> None:
    conn.execute("DELETE FROM topic_events WHERE event_pk = ?", (event_pk,))
    for topic_key in topic_keys:
        conn.execute(
            "INSERT OR IGNORE INTO topic_events (topic_pk, event_pk) SELECT pk, ? FROM topics WHERE key = ?",
            (event_pk, topic_key),
        )


# Topics and links changed by hand (issue #26). The home topic of an event never changes.

TOPIC_FIELDS = ("title", "kind", "summary")


def _create_topic_in_folder(conn: sqlite3.Connection, topic: Topic, data_dir: Path, actor_id: int, ip: str) -> dict:
    # The new file is named after its key. An existing file of that name is refused, nothing is overwritten.
    with _all_or_nothing(data_dir):
        try:
            create_topic_file(data_dir / f"{topic.key}.yaml", topic)
        except VersionConflict as exc:
            raise Conflict(f"{topic.key}.yaml existiert bereits im Datenordner ({exc}).") from exc
        except StoreError as exc:
            raise Invalid(str(exc)) from exc
        reindex(conn, data_dir)
    accounts.audit(conn, "topic_created", user_id=actor_id, target=topic.key, detail=topic.kind, ip=ip)
    return get_topic(conn, topic.key)


def _update_topic_in_folder(
    conn: sqlite3.Connection, key: str, topic: Topic, data_dir: Path, actor_id: int, ip: str, changed: list[str],
) -> dict:
    located = conn.execute("SELECT path FROM topic_files WHERE topic_key = ?", (key,)).fetchone()
    if located is None:
        raise NotFound(f"Topic {key!r} steht in keiner Datei des Datenordners. Bitte chronicle apply ausführen.")
    with _all_or_nothing(data_dir):
        try:
            update_topic_in_file(data_dir / located["path"], topic,
                                 expected_version=_recorded_version(conn, located["path"]))
        except VersionConflict as exc:
            raise Conflict(
                f"{located['path']} wurde außerhalb von Chronicle geändert. Bitte chronicle apply ausführen. "
                f"Die Änderung wurde nicht gespeichert ({exc})."
            ) from exc
        except StoreError as exc:
            raise Invalid(str(exc)) from exc
        reindex(conn, data_dir)
    if changed:
        accounts.audit(conn, "topic_updated", user_id=actor_id, target=key, detail=",".join(changed), ip=ip)
    return get_topic(conn, key)


def create_topic(conn: sqlite3.Connection, topic: Topic, actor_id: int, ip: str, data_dir: Path | None = None) -> dict:
    """Create a topic. With data_dir its file is written first, and the index follows it (ADR 0008)."""
    if conn.execute("SELECT 1 FROM topics WHERE key = ?", (topic.key,)).fetchone():
        raise Conflict(f"Topic-Key {topic.key!r} ist bereits vergeben")
    if data_dir is not None:
        return _create_topic_in_folder(conn, topic, data_dir, actor_id, ip)
    with conn:
        conn.execute(
            "INSERT INTO topics (key, title, kind, summary) VALUES (?, ?, ?, ?)",
            (topic.key, topic.title, topic.kind, topic.summary),
        )
    accounts.audit(conn, "topic_created", user_id=actor_id, target=topic.key, detail=topic.kind, ip=ip)
    return get_topic(conn, topic.key)


def update_topic(
    conn: sqlite3.Connection, key: str, topic: Topic, actor_id: int, ip: str, data_dir: Path | None = None
) -> dict:
    current = get_topic(conn, key)
    if current is None:
        raise NotFound(f"Topic {key!r} nicht gefunden")
    changed = [name for name in TOPIC_FIELDS if current[name] != getattr(topic, name)]
    if data_dir is not None:
        return _update_topic_in_folder(conn, key, topic, data_dir, actor_id, ip, changed)
    with conn:
        conn.execute(
            """
            UPDATE topics SET title = ?, kind = ?, summary = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
            WHERE key = ?
            """,
            (topic.title, topic.kind, topic.summary, key),
        )
    if changed:
        accounts.audit(conn, "topic_updated", user_id=actor_id, target=key, detail=",".join(changed), ip=ip)
    return get_topic(conn, key)


def topic_delete_plan(conn: sqlite3.Connection, key: str) -> dict:
    """What deleting a topic takes with it: its home events (`events`), and the events elsewhere that those would leave
    without a cause (`blockers`, pairs of the dependent event and the home event that triggers it), and how many
    evidence files the events hold."""
    homes = [row["key"] for row in conn.execute(
        "SELECT key FROM events WHERE home_topic = ? ORDER BY key", (key,)
    ).fetchall()]
    blockers = [(row["dependent"], row["cause"]) for row in conn.execute(
        "SELECT events.key AS dependent, json_each.value AS cause FROM events, json_each(events.triggered_by) "
        "WHERE events.home_topic != ? ORDER BY events.key, json_each.value", (key,)
    ).fetchall() if row["cause"] in homes]
    evidence = conn.execute(
        "SELECT COUNT(*) FROM evidence JOIN events ON events.pk = evidence.event_pk WHERE events.home_topic = ?", (key,)
    ).fetchone()[0]
    return {"events": homes, "blockers": blockers, "evidence": evidence}


def delete_topic(conn: sqlite3.Connection, key: str, actor_id: int, ip: str, data_dir: Path | None = None) -> None:
    """Delete a topic together with the events that have it as their home. Events of other topics that are only linked
    to it stay. If an event of another topic is triggered by one of the deleted events, nothing is deleted."""
    if get_topic(conn, key) is None:
        raise NotFound(f"Topic {key!r} nicht gefunden")
    plan = topic_delete_plan(conn, key)
    if plan["blockers"]:
        pairs = ", ".join(f"{dependent} (ausgelöst von {cause})" for dependent, cause in plan["blockers"])
        raise Conflict(f"Events anderer Topics hängen an Events dieses Topics: {pairs}. "
                       "Zuerst dort den Verweis entfernen, dann lässt sich das Topic löschen.")
    if data_dir is not None:
        return _delete_topic_in_folder(conn, key, plan["events"], data_dir, actor_id, ip)
    with conn:
        for event_key in plan["events"]:
            conn.execute("DELETE FROM events WHERE pk = ?", (_event_pk(conn, event_key),))  # links and evidence cascade
        conn.execute("DELETE FROM topics WHERE key = ?", (key,))  # links cascade
    _audit_topic_deleted(conn, key, plan["events"], actor_id, ip)


def _audit_topic_deleted(conn: sqlite3.Connection, key: str, events: list[str], actor_id: int, ip: str) -> None:
    for event_key in events:
        accounts.audit(conn, "event_deleted", user_id=actor_id, target=event_key, detail=f"mit Topic {key}", ip=ip)
    accounts.audit(conn, "topic_deleted", user_id=actor_id, target=key,
                   detail=f"{len(events)} Events" if events else None, ip=ip)


def _delete_topic_in_folder(
    conn: sqlite3.Connection, key: str, events: list[str], data_dir: Path, actor_id: int, ip: str
) -> None:
    """The topic file goes, and with it its events and the links it holds. Links that other files hold to its events
    are removed first. Evidence files stay in the folder; Git has the earlier state (ADR 0008)."""
    located = conn.execute("SELECT path FROM topic_files WHERE topic_key = ?", (key,)).fetchone()
    if located is None:
        raise NotFound(f"Topic {key!r} steht in keiner Datei des Datenordners. Bitte chronicle apply ausführen.")
    path = data_dir / located["path"]
    with _all_or_nothing(data_dir):
        if file_version(path) != _recorded_version(conn, located["path"]):
            raise Conflict(f"{located['path']} wurde außerhalb von Chronicle geändert. Bitte chronicle apply ausführen. "
                           "Nichts wurde gelöscht.")
        for event_key in events:
            # One file change per event, with the index following: the next change reads the new version of the file.
            elsewhere = set(_linked_elsewhere(conn, event_key))
            if elsewhere:
                _change_links(conn, event_key, add=set(), remove=elsewhere, data_dir=data_dir)
                reindex(conn, data_dir)
        path.unlink()
        reindex(conn, data_dir)  # the events of the file are gone from the folder, so they go from the index
        with conn:
            conn.execute("DELETE FROM topics WHERE key = ?", (key,))
            conn.execute("DELETE FROM topic_files WHERE topic_key = ?", (key,))
            conn.execute("DELETE FROM source_files WHERE path = ?", (located["path"],))
            conn.execute("DELETE FROM event_files WHERE path = ?", (located["path"],))
    _audit_topic_deleted(conn, key, events, actor_id, ip)


def link_event(
    conn: sqlite3.Connection, topic_key: str, event_key: str, actor_id: int, ip: str, data_dir: Path | None = None
) -> None:
    """Link an event to another topic. With data_dir the link is a refs line in that topic's file (ADR 0008)."""
    if data_dir is not None:
        with _all_or_nothing(data_dir):
            if _event_pk(conn, event_key) is None:
                raise NotFound(f"Event {event_key!r} nicht gefunden")
            _change_link(conn, event_key, topic_key, present=True, data_dir=data_dir)
            reindex(conn, data_dir)
    else:
        topic_pk = _topic_pk(conn, topic_key)
        event_pk = _event_pk(conn, event_key)
        if event_pk is None:
            raise NotFound(f"Event {event_key!r} nicht gefunden")
        with conn:
            conn.execute("INSERT OR IGNORE INTO topic_events (topic_pk, event_pk) VALUES (?, ?)", (topic_pk, event_pk))
    accounts.audit(conn, "topic_linked", user_id=actor_id, target=topic_key, detail=event_key, ip=ip)


def unlink_event(
    conn: sqlite3.Connection, topic_key: str, event_key: str, actor_id: int, ip: str, data_dir: Path | None = None
) -> None:
    row = conn.execute("SELECT pk, home_topic FROM events WHERE key = ?", (event_key,)).fetchone()
    if row is None:
        raise NotFound(f"Event {event_key!r} nicht gefunden")
    if row["home_topic"] == topic_key:
        raise Invalid("Das Heimat-Topic eines Events kann nicht entfernt werden")
    if data_dir is not None:
        with _all_or_nothing(data_dir):
            _change_link(conn, event_key, topic_key, present=False, data_dir=data_dir)
            reindex(conn, data_dir)
    else:
        topic_pk = _topic_pk(conn, topic_key)
        with conn:
            conn.execute("DELETE FROM topic_events WHERE topic_pk = ? AND event_pk = ?", (topic_pk, row["pk"]))
    accounts.audit(conn, "topic_unlinked", user_id=actor_id, target=topic_key, detail=event_key, ip=ip)

