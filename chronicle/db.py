"""SQLite storage for incidents, events and evidence metadata."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    id          INTEGER PRIMARY KEY,
    title       TEXT NOT NULL,
    summary     TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS events (
    pk            INTEGER PRIMARY KEY,
    incident_id   INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    event_id      TEXT NOT NULL,
    t0            TEXT NOT NULL,
    phase         TEXT NOT NULL,
    actor         TEXT NOT NULL,
    system        TEXT,
    action        TEXT NOT NULL,
    status        TEXT NOT NULL,
    triggered_by  TEXT,
    details       TEXT NOT NULL DEFAULT '',
    UNIQUE (incident_id, event_id)
);

CREATE TABLE IF NOT EXISTS evidence (
    pk           INTEGER PRIMARY KEY,
    event_pk     INTEGER NOT NULL REFERENCES events(pk) ON DELETE CASCADE,
    file         TEXT NOT NULL,
    type         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_events_incident ON events (incident_id, t0);
CREATE INDEX IF NOT EXISTS idx_evidence_event ON evidence (event_pk);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    """Open the database, enable foreign keys and make sure the schema exists."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn
