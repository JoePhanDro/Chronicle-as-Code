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
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (key, reason)


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
                check_triggers(conn, event.triggered_by, own_key=event.key)
            for key in timeline.refs:
                if event_pk_of(conn, key) is None:
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
        existed = event_pk_of(conn, event.key) is not None
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
            (event.key, *event_values(event), topic.key),
        )
        event_pk = event_pk_of(conn, event.key)
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
