"""Topics and events: idempotent import and read queries. All queries are parameterized."""

from __future__ import annotations

import sqlite3

from .models import Timeline

EVENT_SELECT = """
SELECT events.pk, events.key, events.t0, events.phase, events.actor, events.system, events.action,
       events.status, events.triggered_by, events.details, events.home_topic
FROM events
"""


def import_timeline(conn: sqlite3.Connection, timeline: Timeline) -> str:
    """Create or update a topic and its events. Returns the topic key.

    Importing the same file again leaves the database unchanged. Nothing is deleted:
    events removed from a file are only unlinked from this topic. Any failed check
    rolls back the whole import.
    """
    topic = timeline.topic
    file_keys = {event.key for event in timeline.events}

    with conn:
        for event in timeline.events:
            owner = conn.execute("SELECT home_topic FROM events WHERE key = ?", (event.key,)).fetchone()
            if owner is not None and owner["home_topic"] != topic.key:
                raise ValueError(
                    f"Event {event.key!r} ist im Topic {owner['home_topic']!r} definiert. "
                    f"In {topic.key!r} bitte per refs referenzieren."
                )

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
        topic_pk = conn.execute("SELECT pk FROM topics WHERE key = ?", (topic.key,)).fetchone()["pk"]

        for event in timeline.events:
            conn.execute(
                """
                INSERT INTO events
                    (key, t0, phase, actor, system, action, status, triggered_by, details, home_topic, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
                ON CONFLICT (key) DO UPDATE SET
                    t0 = excluded.t0,
                    phase = excluded.phase,
                    actor = excluded.actor,
                    system = excluded.system,
                    action = excluded.action,
                    status = excluded.status,
                    triggered_by = excluded.triggered_by,
                    details = excluded.details,
                    updated_at = excluded.updated_at
                """,
                (
                    event.key,
                    event.t0.isoformat(),
                    event.phase.value,
                    event.actor,
                    event.system,
                    event.action,
                    event.status.value,
                    event.triggered_by,
                    event.details,
                    topic.key,
                ),
            )
            event_pk = conn.execute("SELECT pk FROM events WHERE key = ?", (event.key,)).fetchone()["pk"]
            conn.execute("DELETE FROM evidence WHERE event_pk = ?", (event_pk,))
            for item in event.evidence:
                conn.execute(
                    "INSERT INTO evidence (event_pk, file, type, description) VALUES (?, ?, ?, ?)",
                    (event_pk, item.file, item.type, item.description),
                )

        for event in timeline.events:
            if event.triggered_by and conn.execute(
                "SELECT 1 FROM events WHERE key = ?", (event.triggered_by,)
            ).fetchone() is None:
                raise ValueError(f"Event {event.key!r}: triggered_by {event.triggered_by!r} ist kein bekannter Event-Key")

        for key in timeline.refs:
            if conn.execute("SELECT 1 FROM events WHERE key = ?", (key,)).fetchone() is None:
                raise ValueError(f"refs: Event {key!r} existiert nicht (zuerst in seinem Topic importieren)")

        member_keys = file_keys | set(timeline.refs)
        conn.execute("DELETE FROM topic_events WHERE topic_pk = ?", (topic_pk,))
        for key in sorted(member_keys):
            conn.execute(
                "INSERT INTO topic_events (topic_pk, event_pk) SELECT ?, pk FROM events WHERE key = ?",
                (topic_pk, key),
            )
    return topic.key


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
    """Events linked to a topic (its own and referenced ones), oldest first."""
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
    topics = conn.execute(
        """
        SELECT topics.key FROM topic_events
        JOIN topics ON topics.pk = topic_events.topic_pk
        JOIN events ON events.pk = topic_events.event_pk
        WHERE events.key = ? ORDER BY topics.key
        """,
        (key,),
    ).fetchall()
    event["topics"] = [row["key"] for row in topics]
    return event
