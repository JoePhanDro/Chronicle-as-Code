"""Topics and events: import, manual create/update/delete and read queries. All queries are parameterized."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from . import accounts
from .models import Event, Timeline

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


def _event_values(event: Event) -> tuple:
    return (
        event.t0.isoformat(),
        event.phase.value,
        event.actor,
        event.system,
        event.action,
        event.status.value,
        event.triggered_by,
        event.details,
    )


def _event_pk(conn: sqlite3.Connection, key: str) -> int | None:
    row = conn.execute("SELECT pk FROM events WHERE key = ?", (key,)).fetchone()
    return row["pk"] if row else None


def _replace_evidence(conn: sqlite3.Connection, event_pk: int, event: Event) -> None:
    conn.execute("DELETE FROM evidence WHERE event_pk = ?", (event_pk,))
    for item in event.evidence:
        conn.execute(
            "INSERT INTO evidence (event_pk, file, type, description) VALUES (?, ?, ?, ?)",
            (event_pk, item.file, item.type, item.description),
        )


def _check_trigger(conn: sqlite3.Connection, triggered_by: str | None, own_key: str | None = None) -> None:
    if triggered_by is None:
        return
    if triggered_by == own_key:
        raise Invalid("Ein Event kann sich nicht selbst auslösen")
    if _event_pk(conn, triggered_by) is None:
        raise Invalid(f"triggered_by {triggered_by!r} ist kein bekannter Event-Key")


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

def import_timeline(conn: sqlite3.Connection, timeline: Timeline) -> ImportReport:
    """Create or update a topic and its events from a topic file.

    Idempotent: the same file again changes nothing. Events edited in the UI (source 'manual') and
    events deleted in the UI (tombstones) are skipped and reported. Links are only added, never removed.
    Any failed check rolls back the whole import.
    """
    topic = timeline.topic
    report = ImportReport(topic=topic.key)

    with conn:
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
                continue

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
        topic_pk = _topic_pk(conn, topic.key)

        for event in timeline.events:
            if any(key == event.key for key, _ in report.skipped):
                continue
            existed = _event_pk(conn, event.key) is not None
            conn.execute(
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
                """,
                (event.key, *_event_values(event), topic.key),
            )
            event_pk = _event_pk(conn, event.key)
            _replace_evidence(conn, event_pk, event)
            (report.updated if existed else report.created).append(event.key)

        for event in timeline.events:
            _check_trigger(conn, event.triggered_by, own_key=event.key)

        for key in timeline.refs:
            if _event_pk(conn, key) is None:
                raise ValueError(f"refs: Event {key!r} existiert nicht (zuerst in seinem Topic importieren)")

        for key in [event.key for event in timeline.events] + list(timeline.refs):
            if any(skipped == key for skipped, _ in report.skipped):
                continue
            conn.execute(
                "INSERT OR IGNORE INTO topic_events (topic_pk, event_pk) SELECT ?, pk FROM events WHERE key = ?",
                (topic_pk, key),
            )
    return report


# Manual changes from the UI or the API

def create_event(conn: sqlite3.Connection, event: Event, topic_keys: list[str], actor_id: int, ip: str) -> dict:
    home = topic_keys[0]
    with conn:
        for key in topic_keys:
            _topic_pk(conn, key)
        if _event_pk(conn, event.key) is not None:
            raise Conflict(f"Event-Key {event.key!r} ist bereits vergeben")
        _check_trigger(conn, event.triggered_by, own_key=event.key)

        conn.execute("DELETE FROM deleted_events WHERE key = ?", (event.key,))
        cursor = conn.execute(
            """
            INSERT INTO events (key, t0, phase, actor, system, action, status, triggered_by, details,
                                home_topic, source, version, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual', 1, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            """,
            (event.key, *_event_values(event), home),
        )
        _replace_evidence(conn, cursor.lastrowid, event)
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
) -> dict:
    row = conn.execute(
        f"SELECT pk, version, home_topic FROM events WHERE key = ?", (key,)
    ).fetchone()
    if row is None:
        raise NotFound(f"Event {key!r} nicht gefunden")
    if row["version"] != expected_version:
        raise Conflict(f"Das Event wurde inzwischen geändert (aktuelle Version {row['version']}). Bitte neu laden.")
    if row["home_topic"] not in topic_keys:
        raise Invalid(f"Das Heimat-Topic {row['home_topic']!r} muss verknüpft bleiben")
    _check_trigger(conn, event.triggered_by, own_key=key)

    old = get_event(conn, key)
    old_topics = _topic_keys_of(conn, row["pk"])
    new_values = {"t0": event.t0.isoformat(), "phase": event.phase.value, "actor": event.actor,
                  "system": event.system, "action": event.action, "status": event.status.value,
                  "triggered_by": event.triggered_by, "details": event.details}
    changed = [name for name in EDITABLE_FIELDS if str(old[name]) != str(new_values[name])]
    if old["evidence"] != [item.model_dump() for item in event.evidence]:
        changed.append("evidence")
    if sorted(old_topics) != sorted(topic_keys):
        changed.append("topics")

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
        _replace_evidence(conn, row["pk"], event)
        _set_links(conn, row["pk"], topic_keys)

    if changed:
        accounts.audit(conn, "event_updated", user_id=actor_id, target=key, detail=",".join(changed), ip=ip)
    return get_event(conn, key)


def delete_event(conn: sqlite3.Connection, key: str, actor_id: int, ip: str) -> None:
    row = conn.execute("SELECT pk FROM events WHERE key = ?", (key,)).fetchone()
    if row is None:
        raise NotFound(f"Event {key!r} nicht gefunden")
    dependents = [item["key"] for item in conn.execute(
        "SELECT key FROM events WHERE triggered_by = ? ORDER BY key", (key,)
    ).fetchall()]
    if dependents:
        raise Conflict(f"Event wird von {', '.join(dependents)} ausgelöst. Zuerst dort den Verweis entfernen.")

    with conn:
        conn.execute("DELETE FROM events WHERE pk = ?", (row["pk"],))  # links and evidence cascade
        conn.execute("INSERT OR REPLACE INTO deleted_events (key) VALUES (?)", (key,))
    accounts.audit(conn, "event_deleted", user_id=actor_id, target=key, ip=ip)


def _set_links(conn: sqlite3.Connection, event_pk: int, topic_keys: list[str]) -> None:
    conn.execute("DELETE FROM topic_events WHERE event_pk = ?", (event_pk,))
    for topic_key in topic_keys:
        conn.execute(
            "INSERT OR IGNORE INTO topic_events (topic_pk, event_pk) SELECT pk, ? FROM topics WHERE key = ?",
            (event_pk, topic_key),
        )


# Reads

def _with_evidence(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> list[dict]:
    events = []
    for row in rows:
        event = {key: row[key] for key in row.keys() if key != "pk"}
        evidence = conn.execute(
            "SELECT file, type, description FROM evidence WHERE event_pk = ? ORDER BY pk", (row["pk"],)
        ).fetchall()
        event["evidence"] = [dict(item) for item in evidence]
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


def list_topic_events(conn: sqlite3.Connection, key: str) -> list[dict]:
    rows = conn.execute(
        EVENT_SELECT
        + """
        JOIN topic_events ON topic_events.event_pk = events.pk
        JOIN topics ON topics.pk = topic_events.topic_pk
        WHERE topics.key = ?
        ORDER BY events.t0, events.pk
        """,
        (key,),
    ).fetchall()
    return _with_evidence(conn, rows)


def list_events(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(EVENT_SELECT + " ORDER BY events.t0, events.pk").fetchall()
    return _with_evidence(conn, rows)


def get_event(conn: sqlite3.Connection, key: str) -> dict | None:
    rows = conn.execute(EVENT_SELECT + " WHERE events.key = ?", (key,)).fetchall()
    if not rows:
        return None
    event = _with_evidence(conn, rows)[0]
    event["topics"] = _topic_keys_of(conn, rows[0]["pk"])
    return event


def list_all_topic_keys(conn: sqlite3.Connection) -> list[str]:
    return [row["key"] for row in conn.execute("SELECT key FROM topics ORDER BY key").fetchall()]
