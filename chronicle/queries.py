"""Read queries on the index: events, topics, evidence, links and the event filter. All queries are parameterized.

Nothing here writes. The repository module re-exports these names, so callers can keep using `repository.get_event`.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .errors import APPLY_HINT, Conflict
from .loader import link_below

EVENT_SELECT = """
SELECT events.pk, events.key, events.t0, events.phase, events.actor, events.system, events.action,
       events.status, events.triggered_by, events.details, events.home_topic, events.source, events.version
FROM events
"""


def triggers_from_db(stored: str | None) -> list[str]:
    """The stored form of triggered_by is a JSON list of keys, or NULL for none (issue #87)."""
    return json.loads(stored) if stored else []


def topic_keys_of(conn: sqlite3.Connection, event_pk: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT topics.key FROM topic_events JOIN topics ON topics.pk = topic_events.topic_pk
        WHERE topic_events.event_pk = ? ORDER BY topics.key
        """,
        (event_pk,),
    ).fetchall()
    return [row["key"] for row in rows]


EVIDENCE_META = "SELECT pk AS id, file, type, description, mime, size, sha256 FROM evidence"


def evidence_meta(conn: sqlite3.Connection, evidence_id: int) -> dict:
    return dict(conn.execute(f"{EVIDENCE_META} WHERE pk = ?", (evidence_id,)).fetchone())


def get_evidence_content(conn: sqlite3.Connection, evidence_id: int, data_dir: Path | None = None) -> dict | None:
    """File name, type and bytes of an evidence item. In file mode the index holds no bytes: they come from the
    data folder, and only if they still match the checksum recorded at the last index (issue #89)."""
    row = conn.execute("SELECT file, mime, content, sha256 FROM evidence WHERE pk = ?", (evidence_id,)).fetchone()
    if row is None:
        return None
    content = row["content"]
    if (content is None and data_dir is not None and link_below(data_dir, data_dir / row["file"]) is None
            and (data_dir / row["file"]).is_file()):
        content = (data_dir / row["file"]).read_bytes()
        if hashlib.sha256(content).hexdigest() != row["sha256"]:
            raise Conflict(f"{row['file']} wurde außerhalb von Chronicle geändert. {APPLY_HINT}")
    return {"file": row["file"], "mime": row["mime"], "content": content}


EVIDENCE_CHUNK = 500  # SQLite limits how many values one statement can bind


def with_evidence(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> list[dict]:
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
        event["triggered_by"] = triggers_from_db(event["triggered_by"])
        event["evidence"] = evidence_of[row["pk"]]
        events.append(event)
    return events


# Order of the topic list when the most pressing topic comes first.
_STATUS_RANK = {"open": 0, "in_progress": 1, "resolved": 2, None: 3}


def most_pressing_status(statuses) -> str | None:
    """The most pressing of the given event statuses (open, then in_progress, then resolved), None for none."""
    found = set(statuses)
    for status in ("open", "in_progress"):
        if status in found:
            return status
    return "resolved" if found else None


def list_topics(conn: sqlite3.Connection, *, by_attention: bool = False) -> list[dict]:
    """All topics with their event count, the time of the latest event (last_event_at, UTC), its phase (phase) and a status.

    The status is the most pressing one among the topic's events (open, then in_progress, then resolved),
    None for a topic without events. By default the list is sorted by key; with by_attention the topics
    with open events come first, newest event first within each group.
    """
    rows = conn.execute(
        """
        SELECT topics.key, topics.title, topics.kind, topics.summary, topics.updated_at,
               COUNT(events.pk) AS event_count,
               MAX(events.t0) AS last_event_at,
               (SELECT latest.phase FROM topic_events AS link JOIN events AS latest ON latest.pk = link.event_pk
                WHERE link.topic_pk = topics.pk ORDER BY latest.t0 DESC, latest.pk DESC LIMIT 1) AS phase,
               COALESCE(SUM(events.status = 'open'), 0) AS open_count,
               COALESCE(SUM(events.status = 'in_progress'), 0) AS in_progress_count
        FROM topics
        LEFT JOIN topic_events ON topic_events.topic_pk = topics.pk
        LEFT JOIN events ON events.pk = topic_events.event_pk
        GROUP BY topics.pk
        ORDER BY topics.key
        """
    ).fetchall()
    topics = []
    for row in rows:
        topic = dict(row)
        open_count, in_progress_count = topic.pop("open_count"), topic.pop("in_progress_count")
        if open_count:
            topic["status"] = "open"
        elif in_progress_count:
            topic["status"] = "in_progress"
        else:
            topic["status"] = "resolved" if topic["event_count"] else None
        topics.append(topic)
    if by_attention:
        # Two stable sorts: newest event first (no events last), then by status. Ties keep the key order.
        topics.sort(key=lambda topic: topic["last_event_at"] or "", reverse=True)
        topics.sort(key=lambda topic: _STATUS_RANK[topic["status"]])
    return topics


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
    return with_evidence(conn, rows)


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
    event = with_evidence(conn, rows)[0]
    event["topics"] = topic_keys_of(conn, rows[0]["pk"])
    return event


def get_event_links(conn: sqlite3.Connection, key: str) -> dict[str, list[dict]]:
    """The events that caused this one (its triggered_by) and the events it triggered, by time.

    Each entry has key, action, t0, phase and status; an unknown key gives two empty lists."""
    fields = "key, action, t0, phase, status"
    own = conn.execute("SELECT triggered_by FROM events WHERE key = ?", (key,)).fetchone()
    cause_keys = triggers_from_db(own["triggered_by"]) if own else []
    causes = []
    if cause_keys:
        marks = ",".join("?" * len(cause_keys))
        causes = [dict(row) for row in conn.execute(
            f"SELECT {fields} FROM events WHERE key IN ({marks}) ORDER BY t0, key", cause_keys)]
    effects = [dict(row) for row in conn.execute(
        f"SELECT {fields} FROM events WHERE EXISTS "
        "(SELECT 1 FROM json_each(events.triggered_by) WHERE json_each.value = ?) ORDER BY t0, key", (key,))]
    return {"causes": causes, "effects": effects}


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
