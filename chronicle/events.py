"""Creating, changing and deleting events. The change goes to the topic file first, the index follows (ADR 0008)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from . import accounts
from .errors import Conflict, Invalid, NotFound
from .folder_writes import all_or_nothing, change_links, linked_elsewhere, recorded_version
from .index import check_triggers, event_pk_of, event_values, reindex
from .loader import find_topic_files
from .models import Event
from .queries import get_event, topic_keys_of
from .store import (
    EventNotInFile,
    StoreError,
    VersionConflict,
    add_event_to_file,
    file_version,
    remove_event_from_file,
    update_event_in_file,
)

EDITABLE_FIELDS = ("t0", "phase", "actor", "system", "action", "status", "triggered_by", "details")


def _update_in_folder(
    conn: sqlite3.Connection, key: str, event: Event, topic_keys: list[str], data_dir: Path,
    actor_id: int, ip: str, changed: list[str],
) -> dict:
    """The event's fields go to its home file, the links to the files of the other topics. Then the index follows."""
    with all_or_nothing(data_dir):
        written = None
        if [name for name in changed if name != "topics"]:
            located = conn.execute("SELECT path FROM event_files WHERE event_key = ?", (key,)).fetchone()
            if located is None:
                raise Invalid(f"Event {key!r} steht in keiner Datei des Datenordners. Bitte chronicle apply ausführen.")
            written = located["path"]
            try:
                update_event_in_file(data_dir / located["path"], key, event,
                                     expected_version=recorded_version(conn, located["path"]))
            except VersionConflict as exc:
                raise Conflict(
                    f"{located['path']} wurde außerhalb von Chronicle geändert. Bitte chronicle apply ausführen. "
                    f"Die Änderung wurde nicht gespeichert ({exc})."
                ) from exc
            except (EventNotInFile, StoreError) as exc:
                raise Invalid(str(exc)) from exc
        if "topics" in changed:
            home = conn.execute("SELECT home_topic FROM events WHERE key = ?", (key,)).fetchone()["home_topic"]
            linked = set(linked_elsewhere(conn, key))
            wanted = set(topic_keys) - {home}
            change_links(conn, key, add=wanted - linked, remove=linked - wanted, data_dir=data_dir)
            reindex(conn, data_dir)
        else:
            _index_field_change(conn, key, event, written, data_dir)
    accounts.audit(conn, "event_updated", user_id=actor_id, target=key, detail=",".join(changed), ip=ip)
    return get_event(conn, key)


def _index_field_change(conn: sqlite3.Connection, key: str, event: Event, written: str, data_dir: Path) -> None:
    """The index after the fields of one event were written to its file (issue #125).

    Only that file changed. If every other file is exactly as the index recorded it, the event's row and the file's
    version are updated, and no file is read again. Otherwise the folder is indexed as a whole, which checks every file.
    """
    if not _folder_as_indexed(conn, data_dir, changed=written):
        reindex(conn, data_dir)
        return
    with conn:
        conn.execute(
            """
            UPDATE events SET t0 = ?, phase = ?, actor = ?, system = ?, action = ?, status = ?,
                              triggered_by = ?, details = ?, version = version + 1,
                              updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
            WHERE key = ?
            """,
            (*event_values(event), key),
        )
        conn.execute("UPDATE source_files SET sha256 = ? WHERE path = ?", (file_version(data_dir / written), written))
        # A full index holds no attachment bytes (keep_content=False, issue #89); the files are the only copy.
        conn.execute("UPDATE evidence SET content = NULL WHERE content IS NOT NULL")


def _folder_as_indexed(conn: sqlite3.Connection, data_dir: Path, *, changed: str) -> bool:
    """True if the topic files on disk are the ones the index recorded, except the file named changed (its new
    version is recorded by the caller). A file that is new, gone or edited outside Chronicle makes this False."""
    recorded = {row["path"]: row["sha256"] for row in conn.execute("SELECT path, sha256 FROM source_files")}
    on_disk = {path.relative_to(data_dir).as_posix(): path for path in find_topic_files(data_dir)}
    if recorded.keys() != on_disk.keys() or changed not in recorded:
        return False
    return all(name == changed or file_version(path) == recorded[name] for name, path in on_disk.items())


def _create_in_folder(
    conn: sqlite3.Connection, event: Event, topic_keys: list[str], data_dir: Path, actor_id: int, ip: str
) -> dict:
    if event_pk_of(conn, event.key) is not None:
        raise Conflict(f"Event-Key {event.key!r} ist bereits vergeben")
    check_triggers(conn, event.triggered_by, own_key=event.key)
    located = conn.execute("SELECT path FROM topic_files WHERE topic_key = ?", (topic_keys[0],)).fetchone()
    if located is None:
        raise NotFound(f"Topic {topic_keys[0]!r} steht in keiner Datei des Datenordners. Bitte chronicle apply ausführen.")
    # The event and its links are one change: if a link or the index fails, nothing of it stays in the files.
    with all_or_nothing(data_dir):
        try:
            add_event_to_file(data_dir / located["path"], event,
                              expected_version=recorded_version(conn, located["path"]))
        except VersionConflict as exc:
            raise Conflict(f"{located['path']} wurde außerhalb von Chronicle geändert. Bitte chronicle apply ausführen. "
                           f"Das Event wurde nicht angelegt ({exc}).") from exc
        except StoreError as exc:
            raise Invalid(str(exc)) from exc
        change_links(conn, event.key, add=set(topic_keys[1:]), remove=set(), data_dir=data_dir)
        reindex(conn, data_dir)
    accounts.audit(conn, "event_created", user_id=actor_id, target=event.key, detail=f"topics={','.join(topic_keys)}", ip=ip)
    return get_event(conn, event.key)


def _delete_in_folder(conn: sqlite3.Connection, key: str, data_dir: Path, actor_id: int, ip: str) -> None:
    located = conn.execute("SELECT path FROM event_files WHERE event_key = ?", (key,)).fetchone()
    if located is None:
        raise Invalid(f"Event {key!r} steht in keiner Datei des Datenordners. Bitte chronicle apply ausführen.")
    with all_or_nothing(data_dir):
        # The links go first: an event without links is still valid, a link to a missing event is not.
        change_links(conn, key, add=set(), remove=set(linked_elsewhere(conn, key)), data_dir=data_dir)
        try:
            remove_event_from_file(data_dir / located["path"], key,
                                   expected_version=recorded_version(conn, located["path"]))
        except VersionConflict as exc:
            raise Conflict(f"{located['path']} wurde außerhalb von Chronicle geändert. Bitte chronicle apply ausführen. "
                           f"Nichts wurde gelöscht ({exc}).") from exc
        except (EventNotInFile, StoreError) as exc:
            raise Invalid(str(exc)) from exc
        reindex(conn, data_dir)
    accounts.audit(conn, "event_deleted", user_id=actor_id, target=key, ip=ip)


def create_event(
    conn: sqlite3.Connection, event: Event, topic_keys: list[str], actor_id: int, ip: str, data_dir: Path
) -> dict:
    """Create an event in its home topic: it is appended to the topic file first, the index follows (ADR 0008)."""
    return _create_in_folder(conn, event, topic_keys, data_dir, actor_id, ip)


def update_event(
    conn: sqlite3.Connection,
    key: str,
    event: Event,
    topic_keys: list[str],
    expected_version: int,
    actor_id: int,
    ip: str,
    data_dir: Path,
) -> dict:
    """Change an event: the change is written to the event's topic file first, and the index is then rebuilt from the
    folder (ADR 0008)."""
    row = conn.execute(
        "SELECT pk, version, home_topic FROM events WHERE key = ?", (key,)
    ).fetchone()
    if row is None:
        raise NotFound(f"Event {key!r} nicht gefunden")
    if row["version"] != expected_version:
        raise Conflict(f"Das Event wurde inzwischen geändert (aktuelle Version {row['version']}). Bitte neu laden.")
    if row["home_topic"] not in topic_keys:
        raise Invalid(f"Das Heimat-Topic {row['home_topic']!r} muss verknüpft bleiben")
    check_triggers(conn, event.triggered_by, own_key=key)

    old = get_event(conn, key)
    old_topics = topic_keys_of(conn, row["pk"])
    new_values = {"t0": event.t0.isoformat(), "phase": event.phase.value, "actor": event.actor,
                  "system": event.system, "action": event.action, "status": event.status.value,
                  "triggered_by": event.triggered_by, "details": event.details}
    changed = [name for name in EDITABLE_FIELDS if str(old[name]) != str(new_values[name])]
    if sorted(old_topics) != sorted(topic_keys):
        changed.append("topics")
    if not changed:
        return old  # saving without changes creates no new revision
    return _update_in_folder(conn, key, event, topic_keys, data_dir, actor_id, ip, changed)


def delete_event(conn: sqlite3.Connection, key: str, actor_id: int, ip: str, data_dir: Path) -> None:
    """Delete an event: it is removed from its topic file first. The file is the record (ADR 0008), and Git has the
    earlier version."""
    if event_pk_of(conn, key) is None:
        raise NotFound(f"Event {key!r} nicht gefunden")
    dependents = [item["key"] for item in conn.execute(
        "SELECT events.key FROM events, json_each(events.triggered_by) WHERE json_each.value = ? ORDER BY events.key",
        (key,),
    ).fetchall()]
    if dependents:
        raise Conflict(f"Event wird von {', '.join(dependents)} ausgelöst. Zuerst dort den Verweis entfernen.")
    _delete_in_folder(conn, key, data_dir, actor_id, ip)
