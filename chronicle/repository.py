"""Read and write access to incidents. All queries are parameterized."""

from __future__ import annotations

import sqlite3

from .models import Timeline


def import_timeline(conn: sqlite3.Connection, timeline: Timeline) -> int:
    """Store a validated timeline as one incident and return its id."""
    with conn:
        cursor = conn.execute(
            "INSERT INTO incidents (title, summary) VALUES (?, ?)",
            (timeline.title, timeline.summary),
        )
        incident_id = cursor.lastrowid

        for event in timeline.events:
            event_cursor = conn.execute(
                """
                INSERT INTO events
                    (incident_id, event_id, t0, phase, actor, system, action, status, triggered_by, details)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    incident_id,
                    event.id,
                    event.t0.isoformat(),
                    event.phase.value,
                    event.actor,
                    event.system,
                    event.action,
                    event.status.value,
                    event.triggered_by,
                    event.details,
                ),
            )
            for item in event.evidence:
                conn.execute(
                    "INSERT INTO evidence (event_pk, file, type, description) VALUES (?, ?, ?, ?)",
                    (event_cursor.lastrowid, item.file, item.type, item.description),
                )
    return incident_id


def list_incidents(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT id, title, summary, created_at FROM incidents ORDER BY id").fetchall()
    return [dict(row) for row in rows]


def get_incident(conn: sqlite3.Connection, incident_id: int) -> dict | None:
    row = conn.execute(
        "SELECT id, title, summary, created_at FROM incidents WHERE id = ?", (incident_id,)
    ).fetchone()
    return dict(row) if row else None


def list_events(conn: sqlite3.Connection, incident_id: int) -> list[dict]:
    """Events of one incident in time order, each with its evidence metadata."""
    event_rows = conn.execute(
        """
        SELECT pk, event_id, t0, phase, actor, system, action, status, triggered_by, details
        FROM events WHERE incident_id = ? ORDER BY t0, pk
        """,
        (incident_id,),
    ).fetchall()

    evidence_rows = conn.execute(
        """
        SELECT evidence.event_pk, evidence.file, evidence.type, evidence.description
        FROM evidence
        JOIN events ON events.pk = evidence.event_pk
        WHERE events.incident_id = ?
        ORDER BY evidence.pk
        """,
        (incident_id,),
    ).fetchall()

    evidence_by_event: dict[int, list[dict]] = {}
    for row in evidence_rows:
        evidence_by_event.setdefault(row["event_pk"], []).append(
            {"file": row["file"], "type": row["type"], "description": row["description"]}
        )

    events = []
    for row in event_rows:
        event = {key: row[key] for key in row.keys() if key != "pk"}
        event["evidence"] = evidence_by_event.get(row["pk"], [])
        events.append(event)
    return events
