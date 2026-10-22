"""SQLite storage: topics, events (many-to-many), evidence metadata, accounts, sessions and audit log."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS topics (
    pk          INTEGER PRIMARY KEY,
    key         TEXT NOT NULL UNIQUE,
    title       TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('incident', 'vulnerability', 'project', 'special', 'other')),
    summary     TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS events (
    pk            INTEGER PRIMARY KEY,
    key           TEXT NOT NULL UNIQUE,
    t0            TEXT NOT NULL,
    phase         TEXT NOT NULL,
    actor         TEXT NOT NULL,
    system        TEXT,
    action        TEXT NOT NULL,
    status        TEXT NOT NULL,
    triggered_by  TEXT,
    details       TEXT NOT NULL DEFAULT '',
    home_topic    TEXT NOT NULL,
    source        TEXT NOT NULL DEFAULT 'import' CHECK (source IN ('import', 'manual')),
    version       INTEGER NOT NULL DEFAULT 1,
    updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Tombstones: events deleted in the UI are not re-created by a later import.
-- snapshot is the last version of the event as JSON (issue 34). It is NULL for tombstones from before that.
CREATE TABLE IF NOT EXISTS deleted_events (
    key         TEXT PRIMARY KEY,
    deleted_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    snapshot    TEXT
);

-- Copies of the evidence of a deleted event, so that restoring the event brings it back with its evidence.
CREATE TABLE IF NOT EXISTS deleted_evidence (
    pk           INTEGER PRIMARY KEY,
    event_key    TEXT NOT NULL REFERENCES deleted_events(key) ON DELETE CASCADE,
    file         TEXT NOT NULL,
    type         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    mime         TEXT,
    size         INTEGER,
    sha256       TEXT,
    content      BLOB
);

CREATE TABLE IF NOT EXISTS topic_events (
    topic_pk  INTEGER NOT NULL REFERENCES topics(pk) ON DELETE CASCADE,
    event_pk  INTEGER NOT NULL REFERENCES events(pk) ON DELETE CASCADE,
    PRIMARY KEY (topic_pk, event_pk)
);

-- Evidence is stored in the database (ADR 0004). content is NULL for rows written before that change;
-- a new import fills it in.
CREATE TABLE IF NOT EXISTS evidence (
    pk           INTEGER PRIMARY KEY,
    event_pk     INTEGER NOT NULL REFERENCES events(pk) ON DELETE CASCADE,
    file         TEXT NOT NULL,
    type         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    mime         TEXT,
    size         INTEGER,
    sha256       TEXT,
    content      BLOB
);

-- Server-wide settings, e.g. the reading lock (issue 13).
CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_t0 ON events (t0);
CREATE INDEX IF NOT EXISTS idx_topic_events_event ON topic_events (event_pk);
CREATE INDEX IF NOT EXISTS idx_evidence_event ON evidence (event_pk);

CREATE TABLE IF NOT EXISTS users (
    id             INTEGER PRIMARY KEY,
    username       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash  TEXT NOT NULL,
    role           TEXT NOT NULL CHECK (role IN ('admin', 'analyst', 'viewer')),
    active         INTEGER NOT NULL DEFAULT 1,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf_token  TEXT NOT NULL,
    expires_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS login_failures (
    key  TEXT NOT NULL,
    ts   INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_login_failures_key ON login_failures (key, ts);

CREATE TABLE IF NOT EXISTS audit_log (
    pk      INTEGER PRIMARY KEY,
    ts      INTEGER NOT NULL,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action  TEXT NOT NULL,
    target  TEXT,
    detail  TEXT,
    ip      TEXT
);
"""


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def _create_schema(conn: sqlite3.Connection) -> None:
    # Statement by statement, so the surrounding transaction is not committed early.
    for statement in SCHEMA.split(";"):
        if statement.strip():
            conn.execute(statement)


def migrate_incident_schema(conn: sqlite3.Connection) -> bool:
    """Convert the schema from ADR 0001 (incidents) to topics and events (ADR 0002).

    Each old incident becomes a topic with kind 'incident' and key 'legacy-<id>'.
    Its events get keys 'legacy-<id>-<old event id>'. Runs once; returns True if it migrated.
    """
    if not _has_table(conn, "incidents") or _has_table(conn, "topics"):
        return False

    conn.execute("BEGIN")
    try:
        _migrate(conn)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return True


def _migrate(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE incidents RENAME TO legacy_incidents")
    conn.execute("ALTER TABLE events RENAME TO legacy_events")
    conn.execute("ALTER TABLE evidence RENAME TO legacy_evidence")
    _create_schema(conn)

    incidents = conn.execute("SELECT id, title, summary FROM legacy_incidents").fetchall()
    for incident in incidents:
        conn.execute(
            "INSERT INTO topics (key, title, kind, summary) VALUES (?, ?, 'incident', ?)",
            (f"legacy-{incident['id']}", incident["title"], incident["summary"]),
        )

    event_pk_map: dict[int, int] = {}
    old_events = conn.execute(
        """
        SELECT legacy_events.pk, legacy_events.incident_id, legacy_events.event_id, legacy_events.t0,
               legacy_events.phase, legacy_events.actor, legacy_events.system, legacy_events.action,
               legacy_events.status, legacy_events.triggered_by, legacy_events.details
        FROM legacy_events
        """
    ).fetchall()
    for old in old_events:
        prefix = f"legacy-{old['incident_id']}"
        cursor = conn.execute(
            """
            INSERT INTO events (key, t0, phase, actor, system, action, status, triggered_by, details, home_topic)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"{prefix}-{old['event_id']}",
                old["t0"],
                old["phase"],
                old["actor"],
                old["system"],
                old["action"],
                old["status"],
                f"{prefix}-{old['triggered_by']}" if old["triggered_by"] else None,
                old["details"],
                prefix,
            ),
        )
        event_pk_map[old["pk"]] = cursor.lastrowid
        conn.execute(
            "INSERT INTO topic_events (topic_pk, event_pk) "
            "SELECT pk, ? FROM topics WHERE key = ?",
            (cursor.lastrowid, prefix),
        )

    for old in conn.execute("SELECT event_pk, file, type, description FROM legacy_evidence").fetchall():
        conn.execute(
            "INSERT INTO evidence (event_pk, file, type, description) VALUES (?, ?, ?, ?)",
            (event_pk_map[old["event_pk"]], old["file"], old["type"], old["description"]),
        )

    conn.execute("DROP TABLE legacy_evidence")
    conn.execute("DROP TABLE legacy_events")
    conn.execute("DROP TABLE legacy_incidents")


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    """Add a column to a table created by an earlier version of the schema."""
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


# Full-text index over the searchable fields of events (issue #30). The triggers keep it in step with every
# write. Each statement runs on its own, because the trigger bodies contain ';'.
SEARCH_INDEX = (
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
        action, details, actor,
        content='events', content_rowid='pk', tokenize='unicode61 remove_diacritics 2'
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS events_fts_insert AFTER INSERT ON events BEGIN
        INSERT INTO events_fts (rowid, action, details, actor) VALUES (new.pk, new.action, new.details, new.actor);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS events_fts_delete AFTER DELETE ON events BEGIN
        INSERT INTO events_fts (events_fts, rowid, action, details, actor)
        VALUES ('delete', old.pk, old.action, old.details, old.actor);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS events_fts_update AFTER UPDATE ON events BEGIN
        INSERT INTO events_fts (events_fts, rowid, action, details, actor)
        VALUES ('delete', old.pk, old.action, old.details, old.actor);
        INSERT INTO events_fts (rowid, action, details, actor) VALUES (new.pk, new.action, new.details, new.actor);
    END
    """,
)


def _ensure_search_index(conn: sqlite3.Connection) -> None:
    """Create the search index. A database that had none gets it filled from the events it already holds."""
    existed = _has_table(conn, "events_fts")
    for statement in SEARCH_INDEX:
        conn.execute(statement)
    if not existed:
        with conn:
            conn.execute("INSERT INTO events_fts (events_fts) VALUES ('rebuild')")


def connect(path: str | Path) -> sqlite3.Connection:
    """Open the database, enable foreign keys, migrate old data and make sure the schema exists."""
    # One connection per request, never shared between threads; async form handlers may hop threads.
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    migrate_incident_schema(conn)
    _create_schema(conn)
    _ensure_column(conn, "events", "source", "TEXT NOT NULL DEFAULT 'import'")
    _ensure_column(conn, "events", "version", "INTEGER NOT NULL DEFAULT 1")
    _ensure_column(conn, "evidence", "mime", "TEXT")
    _ensure_column(conn, "evidence", "size", "INTEGER")
    _ensure_column(conn, "evidence", "sha256", "TEXT")
    _ensure_column(conn, "evidence", "content", "BLOB")
    _ensure_column(conn, "users", "must_change_password", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "deleted_events", "snapshot", "TEXT")
    _ensure_search_index(conn)
    return conn
