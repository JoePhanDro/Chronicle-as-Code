"""Creating, changing, deleting and linking topics. The change goes to the topic file first, the index follows (ADR 0008)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from . import accounts
from .errors import Conflict, Invalid, NotFound, changed_outside
from .folder_writes import (
    all_or_nothing,
    change_link,
    change_links,
    file_of_topic,
    linked_elsewhere,
    recorded_version,
    store_change,
)
from .index import event_pk_of, reindex
from .models import Topic
from .queries import get_topic
from .store import (
    StoreError,
    VersionConflict,
    create_topic_file,
    file_version,
    update_topic_in_file,
)

TOPIC_FIELDS = ("title", "kind", "summary")


def _create_topic_in_folder(conn: sqlite3.Connection, topic: Topic, data_dir: Path, actor_id: int, ip: str) -> dict:
    # The new file is named after its key. An existing file of that name is refused, nothing is overwritten.
    with all_or_nothing(data_dir):
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
    path = file_of_topic(conn, key)
    with all_or_nothing(data_dir):
        with store_change(path, "Die Änderung wurde nicht gespeichert"):
            update_topic_in_file(data_dir / path, topic, expected_version=recorded_version(conn, path))
        reindex(conn, data_dir)
    if changed:
        accounts.audit(conn, "topic_updated", user_id=actor_id, target=key, detail=",".join(changed), ip=ip)
    return get_topic(conn, key)


def create_topic(conn: sqlite3.Connection, topic: Topic, actor_id: int, ip: str, data_dir: Path) -> dict:
    """Create a topic: its file is written first, and the index follows it (ADR 0008)."""
    if conn.execute("SELECT 1 FROM topics WHERE key = ?", (topic.key,)).fetchone():
        raise Conflict(f"Topic-Key {topic.key!r} ist bereits vergeben")
    return _create_topic_in_folder(conn, topic, data_dir, actor_id, ip)


def update_topic(
    conn: sqlite3.Connection, key: str, topic: Topic, actor_id: int, ip: str, data_dir: Path
) -> dict:
    current = get_topic(conn, key)
    if current is None:
        raise NotFound(f"Topic {key!r} nicht gefunden")
    changed = [name for name in TOPIC_FIELDS if current[name] != getattr(topic, name)]
    return _update_topic_in_folder(conn, key, topic, data_dir, actor_id, ip, changed)


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


def delete_topic(conn: sqlite3.Connection, key: str, actor_id: int, ip: str, data_dir: Path) -> None:
    """Delete a topic together with the events that have it as their home. Events of other topics that are only linked
    to it stay. If an event of another topic is triggered by one of the deleted events, nothing is deleted."""
    if get_topic(conn, key) is None:
        raise NotFound(f"Topic {key!r} nicht gefunden")
    plan = topic_delete_plan(conn, key)
    if plan["blockers"]:
        pairs = ", ".join(f"{dependent} (ausgelöst von {cause})" for dependent, cause in plan["blockers"])
        raise Conflict(f"Events anderer Topics hängen an Events dieses Topics: {pairs}. "
                       "Zuerst dort den Verweis entfernen, dann lässt sich das Topic löschen.")
    _delete_topic_in_folder(conn, key, plan["events"], data_dir, actor_id, ip)


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
    relative = file_of_topic(conn, key)
    path = data_dir / relative
    with all_or_nothing(data_dir):
        if file_version(path) != recorded_version(conn, relative):
            raise changed_outside(relative, "Nichts wurde gelöscht")
        for event_key in events:
            # One file change per event, with the index following: the next change reads the new version of the file.
            elsewhere = set(linked_elsewhere(conn, event_key))
            if elsewhere:
                change_links(conn, event_key, add=set(), remove=elsewhere, data_dir=data_dir)
                reindex(conn, data_dir)
        path.unlink()
        reindex(conn, data_dir)  # the events of the file are gone from the folder, so they go from the index
        with conn:
            conn.execute("DELETE FROM topics WHERE key = ?", (key,))
            conn.execute("DELETE FROM topic_files WHERE topic_key = ?", (key,))
            conn.execute("DELETE FROM source_files WHERE path = ?", (relative,))
            conn.execute("DELETE FROM event_files WHERE path = ?", (relative,))
    _audit_topic_deleted(conn, key, events, actor_id, ip)


def link_event(
    conn: sqlite3.Connection, topic_key: str, event_key: str, actor_id: int, ip: str, data_dir: Path
) -> None:
    """Link an event to another topic: the link is a refs line in that topic's file (ADR 0008)."""
    with all_or_nothing(data_dir):
        if event_pk_of(conn, event_key) is None:
            raise NotFound(f"Event {event_key!r} nicht gefunden")
        change_link(conn, event_key, topic_key, present=True, data_dir=data_dir)
        reindex(conn, data_dir)
    accounts.audit(conn, "topic_linked", user_id=actor_id, target=topic_key, detail=event_key, ip=ip)


def unlink_event(
    conn: sqlite3.Connection, topic_key: str, event_key: str, actor_id: int, ip: str, data_dir: Path
) -> None:
    row = conn.execute("SELECT pk, home_topic FROM events WHERE key = ?", (event_key,)).fetchone()
    if row is None:
        raise NotFound(f"Event {event_key!r} nicht gefunden")
    if row["home_topic"] == topic_key:
        raise Invalid("Das Heimat-Topic eines Events kann nicht entfernt werden")
    with all_or_nothing(data_dir):
        change_link(conn, event_key, topic_key, present=False, data_dir=data_dir)
        reindex(conn, data_dir)
    accounts.audit(conn, "topic_unlinked", user_id=actor_id, target=topic_key, detail=event_key, ip=ip)
