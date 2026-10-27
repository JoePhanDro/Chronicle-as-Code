"""Building the index from topic files: import, reindex and the checks that guard it (ADR 0008).

The index is a copy of the files. Nothing here changes a file."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .check import load_named_folder
from .errors import Invalid, NotFound
from .evidence import Blob
from .models import Event, Timeline
from .queries import triggers_from_db
from .store import (
    file_version,
)


@dataclass
class ImportReport:
    topic: str
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)


def _triggers_to_db(keys: Sequence[str]) -> str | None:
    return json.dumps(list(keys)) if keys else None


def event_values(event: Event) -> tuple:
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


def event_pk_of(conn: sqlite3.Connection, key: str) -> int | None:
    row = conn.execute("SELECT pk FROM events WHERE key = ?", (key,)).fetchone()
    return row["pk"] if row else None


def _replace_import_evidence(
    conn: sqlite3.Connection, event_pk: int, event: Event, blobs: Mapping[str, Blob]
) -> bool:
    """Replace the evidence of an event with what its file names. Each file must have been read and checked by the
    caller. The index keeps path, type, size and checksum, never the bytes: the file in the data folder is the only
    copy (ADR 0010).

    Returns True if the evidence differs from what the index held, so that an unchanged re-index changes nothing.
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
            INSERT INTO evidence (event_pk, file, type, description, mime, size, sha256)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (event_pk, item.file, item.type, item.description, blob.mime, blob.size, blob.sha256),
        )
    after = sorted((item.file, item.type, item.description, blobs[item.file].sha256) for item in event.evidence)
    return before != after


def check_triggers(conn: sqlite3.Connection, triggered_by: Sequence[str], own_key: str | None = None) -> None:
    """Every cause must exist and must not be the event itself. With own_key, cycles are refused too (docs/notation.md, 5c)."""
    for cause in triggered_by:
        if cause == own_key:
            raise Invalid("Ein Event kann sich nicht selbst auslösen")
        if event_pk_of(conn, cause) is None:
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


def import_timeline(conn: sqlite3.Connection, timeline: Timeline, blobs: Mapping[str, Blob] | None = None) -> ImportReport:
    """Import one topic file. See import_timelines."""
    return import_timelines(conn, [(timeline, blobs or {})])[0]


def import_timelines(
    conn: sqlite3.Connection, items: Sequence[tuple[Timeline, Mapping[str, Blob]]]
) -> list[ImportReport]:
    """Create or update topics and their events from topic files, in one transaction.

    `blobs` of each file maps its evidence paths to the checked content (see evidence.check_file).
    Events may refer to each other across files, so all files are written before the references are checked.
    Idempotent: the same files again change nothing. Links are only added, never removed (reindex removes them).
    Any failed check rolls back everything.
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
                check_triggers(conn, event.triggered_by, own_key=event.key)
            for key in timeline.refs:
                if event_pk_of(conn, key) is None:
                    raise ValueError(f"refs: Event {key!r} existiert nicht (in keiner Datei definiert)")

        for timeline, _ in items:
            _link_topic(conn, timeline)
    return reports


def reindex(conn: sqlite3.Connection, data_dir: Path, known_keys: Collection[str] | None = None) -> list[ImportReport]:
    """Make the database match the topic files in data_dir (ADR 0008). Returns the import reports.

    The files are the truth, so events are taken from them. Also records the version of each file and the file each
    event is defined in, which the next edit checks against. Nothing is written if any file fails the checks.
    """
    if known_keys is None:
        known_keys = {row["key"] for row in conn.execute("SELECT key FROM events")}
    return build_index(conn, data_dir, load_named_folder(data_dir, known_keys=known_keys))


def build_index(conn: sqlite3.Connection, data_dir: Path, named: list) -> list[ImportReport]:
    """Make the index match checked folder contents (from load_named_folder). `reindex` and the importer both end here."""
    indexed = {row["event_key"] for row in conn.execute("SELECT event_key FROM event_files")}
    reports = import_timelines(conn, [(timeline, blobs) for _, timeline, blobs in named])
    _record_files(conn, data_dir, named)
    defined = {event.key for _, timeline, _ in named for event in timeline.events}
    wanted = {(timeline.topic.key, key) for _, timeline, _ in named
              for key in [*(event.key for event in timeline.events), *timeline.refs]}
    with conn:
        # An event that was in a file at the last index and is in none now was removed from its file. The index
        # follows.
        for key in indexed - defined:
            conn.execute("DELETE FROM events WHERE key = ?", (key,))
        _remove_stale_links(conn, defined, wanted)
    return reports


def _remove_stale_links(conn: sqlite3.Connection, defined: set[str], wanted: set[tuple[str, str]]) -> None:
    # Links are the files' truth too: a link that is no longer under refs (or on the event) is removed.
    # import_timelines alone only adds links, so this is where removals happen.
    rows = conn.execute(
        "SELECT topics.key AS topic, events.key AS event FROM topic_events "
        "JOIN topics ON topics.pk = topic_events.topic_pk JOIN events ON events.pk = topic_events.event_pk"
    ).fetchall()
    for row in rows:
        if row["event"] in defined and (row["topic"], row["event"]) not in wanted:
            conn.execute(
                "DELETE FROM topic_events WHERE topic_pk = (SELECT pk FROM topics WHERE key = ?) "
                "AND event_pk = (SELECT pk FROM events WHERE key = ?)",
                (row["topic"], row["event"]),
            )


def _record_files(conn: sqlite3.Connection, data_dir: Path, named: list) -> None:
    """Which file defines which topic and event, and the version of each file. The next edit checks against it."""
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


def _refuse_foreign_events(conn: sqlite3.Connection, timeline: Timeline) -> None:
    topic = timeline.topic
    for event in timeline.events:
        row = conn.execute("SELECT home_topic FROM events WHERE key = ?", (event.key,)).fetchone()
        if row is not None and row["home_topic"] != topic.key:
            raise ValueError(
                f"Event {event.key!r} ist im Topic {row['home_topic']!r} definiert. "
                f"In {topic.key!r} bitte per refs referenzieren."
            )


def _upsert_topic(conn: sqlite3.Connection, topic) -> None:
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


def _write_event(
    conn: sqlite3.Connection, topic_key: str, event, blobs: Mapping[str, Blob], report: ImportReport
) -> None:
    existed = event_pk_of(conn, event.key) is not None
    # An existing event is only touched when a field differs, so an unchanged re-import keeps the version
    # (a bump would make an open editor fail with 409).
    cursor = conn.execute(
        """
        INSERT INTO events
            (key, t0, phase, actor, system, action, status, triggered_by, details,
             home_topic, version, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
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
        (event.key, *event_values(event), topic_key),
    )
    event_pk = event_pk_of(conn, event.key)
    evidence_changed = _replace_import_evidence(conn, event_pk, event, blobs)
    if existed and evidence_changed and cursor.rowcount == 0:
        # Evidence counts as an edit of the event, so it bumps the version too.
        conn.execute(
            "UPDATE events SET version = version + 1, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE pk = ?",
            (event_pk,),
        )
    if not existed:
        report.created.append(event.key)
    elif cursor.rowcount or evidence_changed:
        report.updated.append(event.key)


def _write_topic(
    conn: sqlite3.Connection, timeline: Timeline, blobs: Mapping[str, Blob]
) -> ImportReport:
    topic = timeline.topic
    report = ImportReport(topic=topic.key)
    _refuse_foreign_events(conn, timeline)
    _upsert_topic(conn, topic)
    for event in timeline.events:
        _write_event(conn, topic.key, event, blobs, report)
    return report


def _link_topic(conn: sqlite3.Connection, timeline: Timeline) -> None:
    topic_pk = _topic_pk(conn, timeline.topic.key)
    for key in [event.key for event in timeline.events] + list(timeline.refs):
        conn.execute(
            "INSERT OR IGNORE INTO topic_events (topic_pk, event_pk) SELECT ?, pk FROM events WHERE key = ?",
            (topic_pk, key),
        )
