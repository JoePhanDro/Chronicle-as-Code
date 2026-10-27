"""What every change of the data folder shares: one lock for the whole step, a rollback of the files, and the link
lines (refs) of other topics (ADR 0008, issue #126)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path

from .errors import APPLY_HINT, Conflict, Invalid, NotFound, changed_outside, not_in_a_file
from .loader import TOPIC_FILE_PATTERNS, TimelineError
from .store import (
    StoreError,
    VersionConflict,
    folder_lock,
    restore_file,
    set_ref_in_file,
)


def recorded_version(conn: sqlite3.Connection, path: str) -> str:
    row = conn.execute("SELECT sha256 FROM source_files WHERE path = ?", (path,)).fetchone()
    if row is None:
        raise Conflict(f"{path} ist im Index nicht bekannt. {APPLY_HINT}")
    return row["sha256"]


@contextmanager
def all_or_nothing(data_dir: Path) -> Iterator[list[Path]]:
    """Run a change of the data folder as one step: the files are written, the index follows (ADR 0008).

    If anything fails, the topic files go back to how they were read and the files the block created are removed.
    The yielded list collects those created files. An index that refuses the folder is reported as Invalid.
    The folder stays locked for the whole block: a change that runs meanwhile would otherwise be taken back by the
    rollback of this one (issue #126).
    """
    with folder_lock(data_dir):
        before = {path: path.read_bytes() for pattern in TOPIC_FILE_PATTERNS for path in data_dir.rglob(pattern)}
        created: list[Path] = []
        try:
            yield created
        except StoreError as exc:
            _restore_folder(data_dir, before, created)
            raise Invalid(str(exc)) from exc
        except TimelineError as exc:
            _restore_folder(data_dir, before, created)
            raise Invalid(f"Nichts wurde geändert, weil der Datenordner nicht gültig ist:\n{exc}") from exc
        except BaseException:
            _restore_folder(data_dir, before, created)
            raise


def _restore_folder(data_dir: Path, before: Mapping[Path, bytes], created: Sequence[Path]) -> None:
    for path in created:
        path.unlink(missing_ok=True)
    for pattern in TOPIC_FILE_PATTERNS:
        for path in data_dir.rglob(pattern):
            if path not in before:
                path.unlink(missing_ok=True)
    for path, data in before.items():
        if not path.exists() or path.read_bytes() != data:
            restore_file(path, data)


def linked_elsewhere(conn: sqlite3.Connection, key: str) -> list[str]:
    """The topics an event is linked to besides its home topic, i.e. where it appears under refs."""
    return [item["key"] for item in conn.execute(
        "SELECT topics.key FROM topic_events JOIN topics ON topics.pk = topic_events.topic_pk "
        "JOIN events ON events.pk = topic_events.event_pk WHERE events.key = ? AND topics.key != events.home_topic "
        "ORDER BY topics.key",
        (key,),
    ).fetchall()]


def file_of_event(conn: sqlite3.Connection, key: str) -> str:
    """The path (relative to the data folder) of the file that holds the event."""
    located = conn.execute("SELECT path FROM event_files WHERE event_key = ?", (key,)).fetchone()
    if located is None:
        raise Invalid(not_in_a_file(f"Event {key!r}"))
    return located["path"]


def file_of_topic(conn: sqlite3.Connection, key: str) -> str:
    """The path (relative to the data folder) of the file of the topic."""
    located = conn.execute("SELECT path FROM topic_files WHERE topic_key = ?", (key,)).fetchone()
    if located is None:
        raise NotFound(not_in_a_file(f"Topic {key!r}"))
    return located["path"]


@contextmanager
def store_change(path: str, outcome: str, *, also_invalid: tuple[type[Exception], ...] = ()) -> Iterator[None]:
    """A change of a topic file. A file that changed outside Chronicle is a Conflict naming `outcome`; any other
    refusal of the store is Invalid."""
    try:
        yield
    except VersionConflict as exc:
        raise changed_outside(path, outcome, exc) from exc
    except (StoreError, *also_invalid) as exc:
        raise Invalid(str(exc)) from exc


def change_link(conn: sqlite3.Connection, key: str, topic_key: str, *, present: bool, data_dir: Path) -> None:
    path = file_of_topic(conn, topic_key)
    with store_change(path, "Der Verweis wurde nicht geändert"):
        set_ref_in_file(data_dir / path, key, present=present, expected_version=recorded_version(conn, path))


def change_links(conn: sqlite3.Connection, key: str, *, add, remove, data_dir: Path) -> None:
    """Link an event to topics (add) and unlink it from topics (remove). Each change is one topic file."""
    for topic_key in sorted(add):
        change_link(conn, key, topic_key, present=True, data_dir=data_dir)
    for topic_key in sorted(remove):
        change_link(conn, key, topic_key, present=False, data_dir=data_dir)
