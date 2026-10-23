"""Topics and events: import, manual create/update/delete and read queries. All queries are parameterized."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import accounts
from .check import load_named_folder
from .evidence import EVIDENCE_TYPES, MAX_DESCRIPTION_LENGTH, Blob
from .models import Event, Timeline, Topic
from .store import (
    EventNotInFile,
    StoreError,
    VersionConflict,
    add_event_to_file,
    file_version,
    remove_event_from_file,
    update_event_in_file,
)

EVENT_SELECT = """
SELECT events.pk, events.key, events.t0, events.phase, events.actor, events.system, events.action,
       events.status, events.triggered_by, events.details, events.home_topic, events.source, events.version
FROM events
"""

EDITABLE_FIELDS = ("t0", "phase", "actor", "system", "action", "status", "triggered_by", "details")


class RepositoryError(Exception):
    """Base class for errors the API maps to HTTP status codes."""


class NotFound(RepositoryError):
    pass


class Conflict(RepositoryError):
    pass


class Invalid(RepositoryError, ValueError):
    pass


@dataclass
class ImportReport:
    topic: str
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (key, reason)


def _triggers_from_db(stored: str | None) -> list[str]:
    """The stored form of triggered_by is a JSON list of keys, or NULL for none (issue #87)."""
    return json.loads(stored) if stored else []


def _triggers_to_db(keys: Sequence[str]) -> str | None:
    return json.dumps(list(keys)) if keys else None


def _cause_keys(value: object) -> list[str]:
    """Causes as a tombstone snapshot holds them. Snapshots from before issue #87 hold one key, or nothing."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return list(value)


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


def _replace_import_evidence(conn: sqlite3.Connection, event_pk: int, event: Event, blobs: Mapping[str, Blob]) -> bool:
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
            (event_pk, item.file, item.type, item.description, blob.mime, blob.size, blob.sha256, blob.content),
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
            pending.extend(_triggers_from_db(row["triggered_by"]))
    return False


def _topic_pk(conn: sqlite3.Connection, key: str) -> int:
    row = conn.execute("SELECT pk FROM topics WHERE key = ?", (key,)).fetchone()
    if row is None:
        raise NotFound(f"Topic {key!r} nicht gefunden")
    return row["pk"]


def _topic_keys_of(conn: sqlite3.Connection, event_pk: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT topics.key FROM topic_events JOIN topics ON topics.pk = topic_events.topic_pk
        WHERE topic_events.event_pk = ? ORDER BY topics.key
        """,
        (event_pk,),
    ).fetchall()
    return [row["key"] for row in rows]


# Import

def import_timeline(conn: sqlite3.Connection, timeline: Timeline, blobs: Mapping[str, Blob] | None = None) -> ImportReport:
    """Import one topic file. See import_timelines."""
    return import_timelines(conn, [(timeline, blobs or {})])[0]


def import_timelines(
    conn: sqlite3.Connection, items: Sequence[tuple[Timeline, Mapping[str, Blob]]]
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
        reports = [_write_topic(conn, timeline, blobs) for timeline, blobs in items]

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
    reports = write_index(conn, data_dir, named)
    defined = {event.key for _, timeline, _ in named for event in timeline.events}
    with conn:
        # An event that was in a file at the last index and is in none now was removed from its file. The index
        # follows. Events that were never in a file (created in the database) are not touched.
        for key in indexed - defined:
            conn.execute("DELETE FROM events WHERE key = ?", (key,))
    return reports


def write_index(conn: sqlite3.Connection, data_dir: Path, named: list) -> list[ImportReport]:
    """Write checked folder contents (from load_named_folder) into the index. Events edited in the app stay
    'manual' and are kept back, as before (ADR 0003). See reindex for the file mode."""
    items = [(timeline, blobs) for _, timeline, blobs in named]
    reports = import_timelines(conn, items)
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


def _update_in_folder(
    conn: sqlite3.Connection, key: str, event: Event, data_dir: Path, actor_id: int, ip: str, changed: list[str]
) -> dict:
    located = conn.execute("SELECT path FROM event_files WHERE event_key = ?", (key,)).fetchone()
    if located is None:
        raise Invalid(f"Event {key!r} steht in keiner Datei des Datenordners. Bitte chronicle apply ausführen.")
    recorded = conn.execute("SELECT sha256 FROM source_files WHERE path = ?", (located["path"],)).fetchone()
    if recorded is None:
        raise Conflict(f"{located['path']} ist im Index nicht bekannt. Bitte chronicle apply ausführen.")
    try:
        update_event_in_file(data_dir / located["path"], key, event, expected_version=recorded["sha256"])
    except VersionConflict as exc:
        raise Conflict(
            f"{located['path']} wurde außerhalb von Chronicle geändert. Bitte chronicle apply ausführen. "
            f"Die Änderung wurde nicht gespeichert ({exc})."
        ) from exc
    except (EventNotInFile, StoreError) as exc:
        raise Invalid(str(exc)) from exc
    reindex(conn, data_dir)
    accounts.audit(conn, "event_updated", user_id=actor_id, target=key, detail=",".join(changed), ip=ip)
    return get_event(conn, key)


def _write_topic(conn: sqlite3.Connection, timeline: Timeline, blobs: Mapping[str, Blob]) -> ImportReport:
    topic = timeline.topic
    report = ImportReport(topic=topic.key)

    for event in timeline.events:
        if conn.execute("SELECT 1 FROM deleted_events WHERE key = ?", (event.key,)).fetchone():
            report.skipped.append((event.key, "in der Oberfläche gelöscht"))
            continue
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
        evidence_changed = _replace_import_evidence(conn, event_pk, event, blobs)
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


def _create_in_folder(
    conn: sqlite3.Connection, event: Event, topic_keys: list[str], data_dir: Path, actor_id: int, ip: str
) -> dict:
    if len(topic_keys) != 1:
        raise Invalid("Im Dateimodus gehört ein neues Event zu genau einem Topic. Verknüpfungen folgen (Issue #89).")
    if _event_pk(conn, event.key) is not None:
        raise Conflict(f"Event-Key {event.key!r} ist bereits vergeben")
    _check_triggers(conn, event.triggered_by, own_key=event.key)
    located = conn.execute("SELECT path FROM topic_files WHERE topic_key = ?", (topic_keys[0],)).fetchone()
    if located is None:
        raise NotFound(f"Topic {topic_keys[0]!r} steht in keiner Datei des Datenordners. Bitte chronicle apply ausführen.")
    try:
        add_event_to_file(data_dir / located["path"], event,
                          expected_version=_recorded_version(conn, located["path"]))
    except VersionConflict as exc:
        raise Conflict(f"{located['path']} wurde außerhalb von Chronicle geändert. Bitte chronicle apply ausführen. "
                       f"Das Event wurde nicht angelegt ({exc}).") from exc
    except StoreError as exc:
        raise Invalid(str(exc)) from exc
    reindex(conn, data_dir)
    accounts.audit(conn, "event_created", user_id=actor_id, target=event.key, detail=f"topics={topic_keys[0]}", ip=ip)
    return get_event(conn, event.key)


def _delete_in_folder(conn: sqlite3.Connection, key: str, data_dir: Path, actor_id: int, ip: str) -> None:
    located = conn.execute("SELECT path FROM event_files WHERE event_key = ?", (key,)).fetchone()
    if located is None:
        raise Invalid(f"Event {key!r} steht in keiner Datei des Datenordners. Bitte chronicle apply ausführen.")
    elsewhere = [item["key"] for item in conn.execute(
        "SELECT topics.key FROM topic_events JOIN topics ON topics.pk = topic_events.topic_pk "
        "JOIN events ON events.pk = topic_events.event_pk WHERE events.key = ? AND topics.key != events.home_topic "
        "ORDER BY topics.key",
        (key,),
    ).fetchall()]
    if elsewhere:
        raise Conflict(f"Event ist auch mit {', '.join(elsewhere)} verknüpft. Den Verweis in deren Datei entfernen, "
                       "dann erneut löschen.")
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

        conn.execute("DELETE FROM deleted_events WHERE key = ?", (event.key,))
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
    old_topics = _topic_keys_of(conn, row["pk"])
    new_values = {"t0": event.t0.isoformat(), "phase": event.phase.value, "actor": event.actor,
                  "system": event.system, "action": event.action, "status": event.status.value,
                  "triggered_by": event.triggered_by, "details": event.details}
    changed = [name for name in EDITABLE_FIELDS if str(old[name]) != str(new_values[name])]
    if sorted(old_topics) != sorted(topic_keys):
        changed.append("topics")
    if not changed:
        return old  # saving without changes creates no new revision
    if data_dir is not None:
        if "topics" in changed:
            raise Invalid("Verknüpfungen lassen sich im Dateimodus noch nicht ändern (Issue #89, folgt).")
        return _update_in_folder(conn, key, event, data_dir, actor_id, ip, changed)

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


FILE_MODE_EVIDENCE_TEXT = "Anhänge lassen sich im Dateimodus noch nicht ändern (Issue #89, folgt)."


def add_evidence(
    conn: sqlite3.Connection, key: str, blob: Blob, evidence_type: str, description: str, actor_id: int, ip: str,
    data_dir: Path | None = None,
) -> dict:
    if data_dir is not None:
        # Evidence lives in the database here. An index rebuild from the files would replace it silently.
        raise Invalid(FILE_MODE_EVIDENCE_TEXT)
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
    return _evidence_meta(conn, cursor.lastrowid)


def delete_evidence(conn: sqlite3.Connection, evidence_id: int, actor_id: int, ip: str, data_dir: Path | None = None) -> None:
    if data_dir is not None:
        raise Invalid(FILE_MODE_EVIDENCE_TEXT)
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
    """Delete an event. With data_dir it is removed from its topic file first, and no tombstone is kept: the file
    is the record now (ADR 0008). Without it the database keeps a tombstone, as before."""
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

    # Copied before the event goes, so that the tombstone can bring it back (issue #34).
    snapshot = _snapshot(conn, row["pk"])
    evidence = conn.execute(
        "SELECT file, type, description, mime, size, sha256, content FROM evidence WHERE event_pk = ? ORDER BY pk",
        (row["pk"],),
    ).fetchall()
    with conn:
        conn.execute("DELETE FROM events WHERE pk = ?", (row["pk"],))  # links and evidence cascade
        conn.execute("DELETE FROM deleted_evidence WHERE event_key = ?", (key,))
        conn.execute(
            """
            INSERT INTO deleted_events (key, snapshot) VALUES (?, ?)
            ON CONFLICT (key) DO UPDATE SET snapshot = excluded.snapshot,
                deleted_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
            """,
            (key, snapshot),
        )
        conn.executemany(
            "INSERT INTO deleted_evidence (event_key, file, type, description, mime, size, sha256, content) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(key, *item) for item in evidence],
        )
    accounts.audit(conn, "event_deleted", user_id=actor_id, target=key, ip=ip)


def _snapshot(conn: sqlite3.Connection, event_pk: int) -> str:
    row = conn.execute(EVENT_SELECT + " WHERE events.pk = ?", (event_pk,)).fetchone()
    event = {name: row[name] for name in row.keys() if name != "pk"}
    event["triggered_by"] = _triggers_from_db(event["triggered_by"])
    event["topics"] = _topic_keys_of(conn, event_pk)
    return json.dumps(event, ensure_ascii=False)


# Tombstones (issue #34). A restored event is created again from its last version. Links to topics and
# triggers that no longer exist are left out; the caller gets them back to show.

def list_tombstones(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT key, deleted_at, snapshot,
               (SELECT COUNT(*) FROM deleted_evidence WHERE deleted_evidence.event_key = deleted_events.key) AS evidence
        FROM deleted_events ORDER BY deleted_at DESC, key
        """
    ).fetchall()
    tombstones = []
    for row in rows:
        last = json.loads(row["snapshot"]) if row["snapshot"] else {}
        tombstones.append({
            "key": row["key"], "deleted_at": row["deleted_at"], "restorable": row["snapshot"] is not None,
            "action": last.get("action"), "actor": last.get("actor"), "t0": last.get("t0"),
            "home_topic": last.get("home_topic"), "evidence": row["evidence"],
        })
    return tombstones


def restore_event(conn: sqlite3.Connection, key: str, actor_id: int, ip: str) -> tuple[dict, list[str]]:
    tombstone = conn.execute("SELECT snapshot FROM deleted_events WHERE key = ?", (key,)).fetchone()
    if tombstone is None:
        raise NotFound(f"Grabstein für {key!r} nicht gefunden")
    if tombstone["snapshot"] is None:
        raise Conflict(
            "Für diesen Grabstein ist keine letzte Fassung gespeichert. Er lässt sich nur endgültig entfernen; "
            "der nächste Import nimmt das Event dann wieder auf, wenn es in der YAML steht."
        )
    if _event_pk(conn, key) is not None:
        raise Conflict(f"Event-Key {key!r} ist inzwischen vergeben")
    last = json.loads(tombstone["snapshot"])

    home = last["home_topic"]
    if conn.execute("SELECT 1 FROM topics WHERE key = ?", (home,)).fetchone() is None:
        raise Conflict(f"Das Heimat-Topic {home!r} existiert nicht mehr. Zuerst das Topic wieder anlegen.")

    dropped: list[str] = []
    topic_keys = [home]
    for topic_key in last["topics"]:
        if topic_key == home:
            continue
        if conn.execute("SELECT 1 FROM topics WHERE key = ?", (topic_key,)).fetchone():
            topic_keys.append(topic_key)
        else:
            dropped.append(f"Topic {topic_key}")
    causes: list[str] = []
    for cause in _cause_keys(last["triggered_by"]):
        if _event_pk(conn, cause) is None:
            dropped.append(f"Auslöser {cause}")
        else:
            causes.append(cause)

    evidence = conn.execute(
        "SELECT file, type, description, mime, size, sha256, content FROM deleted_evidence WHERE event_key = ? ORDER BY pk",
        (key,),
    ).fetchall()
    with conn:
        conn.execute("DELETE FROM deleted_events WHERE key = ?", (key,))  # the evidence copies go with it
        cursor = conn.execute(
            """
            INSERT INTO events (key, t0, phase, actor, system, action, status, triggered_by, details,
                                home_topic, source, version, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            """,
            (key, last["t0"], last["phase"], last["actor"], last["system"], last["action"], last["status"],
             _triggers_to_db(causes), last["details"], home, last["source"], last["version"] + 1),
        )
        _set_links(conn, cursor.lastrowid, topic_keys)
        conn.executemany(
            "INSERT INTO evidence (event_pk, file, type, description, mime, size, sha256, content) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(cursor.lastrowid, *item) for item in evidence],
        )
    accounts.audit(conn, "event_restored", user_id=actor_id, target=key, detail=", ".join(dropped) or None, ip=ip)
    return get_event(conn, key), dropped


def purge_tombstone(conn: sqlite3.Connection, key: str, actor_id: int, ip: str) -> None:
    """Removes the tombstone for good. The event can be imported again from the YAML, if it is still there."""
    if conn.execute("SELECT 1 FROM deleted_events WHERE key = ?", (key,)).fetchone() is None:
        raise NotFound(f"Grabstein für {key!r} nicht gefunden")
    with conn:
        conn.execute("DELETE FROM deleted_events WHERE key = ?", (key,))  # evidence copies cascade
    accounts.audit(conn, "tombstone_purged", user_id=actor_id, target=key, ip=ip)


def _set_links(conn: sqlite3.Connection, event_pk: int, topic_keys: list[str]) -> None:
    conn.execute("DELETE FROM topic_events WHERE event_pk = ?", (event_pk,))
    for topic_key in topic_keys:
        conn.execute(
            "INSERT OR IGNORE INTO topic_events (topic_pk, event_pk) SELECT pk, ? FROM topics WHERE key = ?",
            (event_pk, topic_key),
        )


# Reads

EVIDENCE_META = "SELECT pk AS id, file, type, description, mime, size, sha256 FROM evidence"


def _evidence_meta(conn: sqlite3.Connection, evidence_id: int) -> dict:
    return dict(conn.execute(f"{EVIDENCE_META} WHERE pk = ?", (evidence_id,)).fetchone())


def get_evidence_content(conn: sqlite3.Connection, evidence_id: int) -> dict | None:
    row = conn.execute("SELECT file, mime, content FROM evidence WHERE pk = ?", (evidence_id,)).fetchone()
    return dict(row) if row else None


EVIDENCE_CHUNK = 500  # SQLite limits how many values one statement can bind


def _with_evidence(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> list[dict]:
    """Events with their evidence. The evidence of many events comes in one query per chunk, not one per event."""
    pks = [row["pk"] for row in rows]
    evidence_of: dict[int, list[dict]] = {pk: [] for pk in pks}
    for start in range(0, len(pks), EVIDENCE_CHUNK):
        chunk = pks[start : start + EVIDENCE_CHUNK]
        placeholders = ", ".join("?" * len(chunk))
        found = conn.execute(
            f"SELECT event_pk, pk AS id, file, type, description, mime, size, sha256 FROM evidence "
            f"WHERE event_pk IN ({placeholders}) ORDER BY pk",
            chunk,
        ).fetchall()
        for item in found:
            evidence_of[item["event_pk"]].append({key: item[key] for key in item.keys() if key != "event_pk"})
    events = []
    for row in rows:
        event = {key: row[key] for key in row.keys() if key != "pk"}
        event["triggered_by"] = _triggers_from_db(event["triggered_by"])
        event["evidence"] = evidence_of[row["pk"]]
        events.append(event)
    return events


def list_topics(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT topics.key, topics.title, topics.kind, topics.summary, topics.updated_at,
               (SELECT COUNT(*) FROM topic_events WHERE topic_events.topic_pk = topics.pk) AS event_count
        FROM topics ORDER BY topics.key
        """
    ).fetchall()
    return [dict(row) for row in rows]


def get_topic(conn: sqlite3.Connection, key: str) -> dict | None:
    row = conn.execute(
        "SELECT key, title, kind, summary, updated_at FROM topics WHERE key = ?", (key,)
    ).fetchone()
    return dict(row) if row else None


@dataclass(frozen=True)
class EventFilter:
    """Conditions for the event list. None or an empty value means no condition."""

    topic: str | None = None  # events linked to this topic, home and referenced events alike
    query: str | None = None  # words to find in action, details and actor; each word matches as a prefix
    phase: str | None = None
    status: str | None = None
    actor: str | None = None  # part of the actor name, not case-sensitive
    system: str | None = None  # part of the system name, not case-sensitive
    since: date | None = None  # t0 on or after this day (UTC)
    until: date | None = None  # t0 on or before this day (UTC)


def _match_expression(text: str) -> str | None:
    # Every word becomes a quoted prefix term, so characters with a meaning in FTS5 syntax cannot break the query.
    words = re.findall(r"\w+", text)
    return " ".join(f'"{word}"*' for word in words) or None


def _filter_sql(flt: EventFilter) -> tuple[str, list]:
    clauses: list[str] = []
    params: list = []
    if flt.topic is not None:
        clauses.append(
            "events.pk IN (SELECT topic_events.event_pk FROM topic_events "
            "JOIN topics ON topics.pk = topic_events.topic_pk WHERE topics.key = ?)"
        )
        params.append(flt.topic)
    expression = _match_expression(flt.query or "")
    if expression is not None:
        clauses.append("events.pk IN (SELECT rowid FROM events_fts WHERE events_fts MATCH ?)")
        params.append(expression)
    if flt.phase:
        clauses.append("events.phase = ?")
        params.append(flt.phase)
    if flt.status:
        clauses.append("events.status = ?")
        params.append(flt.status)
    if flt.actor:
        clauses.append("instr(lower(events.actor), lower(?)) > 0")
        params.append(flt.actor)
    if flt.system:
        clauses.append("instr(lower(events.system), lower(?)) > 0")
        params.append(flt.system)
    # t0 is stored in UTC, so the first ten characters are the UTC day.
    if flt.since is not None:
        clauses.append("substr(events.t0, 1, 10) >= ?")
        params.append(flt.since.isoformat())
    if flt.until is not None:
        clauses.append("substr(events.t0, 1, 10) <= ?")
        params.append(flt.until.isoformat())
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


def _page(limit: int | None, offset: int) -> tuple[int, int]:
    # SQLite reads LIMIT -1 as "no limit", and OFFSET needs a LIMIT clause.
    return (-1 if limit is None else limit), offset


def find_events(conn: sqlite3.Connection, flt: EventFilter, *, limit: int | None = None, offset: int = 0) -> list[dict]:
    """Events that match `flt`, in time order. `limit` and `offset` page the result."""
    where, params = _filter_sql(flt)
    rows = conn.execute(
        EVENT_SELECT + where + " ORDER BY events.t0, events.pk LIMIT ? OFFSET ?",
        (*params, *_page(limit, offset)),
    ).fetchall()
    return _with_evidence(conn, rows)


def count_events(conn: sqlite3.Connection, flt: EventFilter) -> int:
    where, params = _filter_sql(flt)
    return conn.execute(f"SELECT COUNT(*) FROM events{where}", params).fetchone()[0]


def list_topic_events(conn: sqlite3.Connection, key: str, *, limit: int | None = None, offset: int = 0) -> list[dict]:
    return find_events(conn, EventFilter(topic=key), limit=limit, offset=offset)


def list_events(conn: sqlite3.Connection, topic: str | None = None, *, limit: int | None = None, offset: int = 0) -> list[dict]:
    """All events, or only those linked to `topic` (home and referenced events alike). `limit` and `offset` page the result."""
    return find_events(conn, EventFilter(topic=topic), limit=limit, offset=offset)


def get_event(conn: sqlite3.Connection, key: str) -> dict | None:
    rows = conn.execute(EVENT_SELECT + " WHERE events.key = ?", (key,)).fetchall()
    if not rows:
        return None
    event = _with_evidence(conn, rows)[0]
    event["topics"] = _topic_keys_of(conn, rows[0]["pk"])
    return event


def search_actors(conn: sqlite3.Connection, text: str, limit: int = 20) -> list[str]:
    """Actor names that contain `text`, not case-sensitive, alphabetically."""
    rows = conn.execute(
        "SELECT DISTINCT actor FROM events WHERE instr(lower(actor), lower(?)) > 0 ORDER BY actor LIMIT ?",
        (text, limit),
    ).fetchall()
    return [row["actor"] for row in rows]


def search_systems(conn: sqlite3.Connection, text: str, limit: int = 20) -> list[str]:
    """System names that contain `text`, not case-sensitive, alphabetically."""
    rows = conn.execute(
        "SELECT DISTINCT system FROM events WHERE system IS NOT NULL AND instr(lower(system), lower(?)) > 0 "
        "ORDER BY system LIMIT ?",
        (text, limit),
    ).fetchall()
    return [row["system"] for row in rows]


def list_all_topic_keys(conn: sqlite3.Connection) -> list[str]:
    return [row["key"] for row in conn.execute("SELECT key FROM topics ORDER BY key").fetchall()]


# Topics and links changed by hand (issue #26). The home topic of an event never changes.

TOPIC_FIELDS = ("title", "kind", "summary")


def create_topic(conn: sqlite3.Connection, topic: Topic, actor_id: int, ip: str) -> dict:
    with conn:
        if conn.execute("SELECT 1 FROM topics WHERE key = ?", (topic.key,)).fetchone():
            raise Conflict(f"Topic-Key {topic.key!r} ist bereits vergeben")
        conn.execute(
            "INSERT INTO topics (key, title, kind, summary) VALUES (?, ?, ?, ?)",
            (topic.key, topic.title, topic.kind, topic.summary),
        )
    accounts.audit(conn, "topic_created", user_id=actor_id, target=topic.key, detail=topic.kind, ip=ip)
    return get_topic(conn, topic.key)


def update_topic(conn: sqlite3.Connection, key: str, topic: Topic, actor_id: int, ip: str) -> dict:
    current = get_topic(conn, key)
    if current is None:
        raise NotFound(f"Topic {key!r} nicht gefunden")
    changed = [name for name in TOPIC_FIELDS if current[name] != getattr(topic, name)]
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


def delete_topic(conn: sqlite3.Connection, key: str, actor_id: int, ip: str) -> None:
    if get_topic(conn, key) is None:
        raise NotFound(f"Topic {key!r} nicht gefunden")
    homes = [row["key"] for row in conn.execute(
        "SELECT key FROM events WHERE home_topic = ? ORDER BY key", (key,)
    ).fetchall()]
    if homes:
        raise Conflict(f"Im Topic liegen noch Heimat-Events: {', '.join(homes)}. Zuerst diese Events löschen.")
    with conn:
        conn.execute("DELETE FROM topics WHERE key = ?", (key,))  # links cascade
    accounts.audit(conn, "topic_deleted", user_id=actor_id, target=key, ip=ip)


def link_event(conn: sqlite3.Connection, topic_key: str, event_key: str, actor_id: int, ip: str) -> None:
    topic_pk = _topic_pk(conn, topic_key)
    event_pk = _event_pk(conn, event_key)
    if event_pk is None:
        raise NotFound(f"Event {event_key!r} nicht gefunden")
    with conn:
        conn.execute("INSERT OR IGNORE INTO topic_events (topic_pk, event_pk) VALUES (?, ?)", (topic_pk, event_pk))
    accounts.audit(conn, "topic_linked", user_id=actor_id, target=topic_key, detail=event_key, ip=ip)


def unlink_event(conn: sqlite3.Connection, topic_key: str, event_key: str, actor_id: int, ip: str) -> None:
    topic_pk = _topic_pk(conn, topic_key)
    row = conn.execute("SELECT pk, home_topic FROM events WHERE key = ?", (event_key,)).fetchone()
    if row is None:
        raise NotFound(f"Event {event_key!r} nicht gefunden")
    if row["home_topic"] == topic_key:
        raise Invalid("Das Heimat-Topic eines Events kann nicht entfernt werden")
    with conn:
        conn.execute("DELETE FROM topic_events WHERE topic_pk = ? AND event_pk = ?", (topic_pk, row["pk"]))
    accounts.audit(conn, "topic_unlinked", user_id=actor_id, target=topic_key, detail=event_key, ip=ip)

