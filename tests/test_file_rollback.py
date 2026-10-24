"""File mode (ADR 0008): a change that the index refuses leaves the data folder as it was, and the caller gets a 422.

The broken file stands in for any file of the folder that fails the checks, for example one edited by hand.
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from chronicle import repository
from chronicle.db import connect
from chronicle.evidence import check_file
from chronicle.store import LOCK_FILE
from chronicle.models import Event
from chronicle.repository import Invalid, NotFound

BROKEN = "topic: [kaputt\n"


@pytest.fixture
def folder(folder_with_b):
    return folder_with_b


def snapshot(folder: Path) -> dict[str, bytes]:
    return {str(path.relative_to(folder)): path.read_bytes()
            for path in folder.rglob("*") if path.is_file() and path.name != LOCK_FILE}


def break_folder(folder: Path) -> None:
    (folder / "kaputt.yaml").write_text(BROKEN, encoding="utf-8")


def new_event(key="neu-1", **changes):
    data = {"key": key, "t0": "2026-10-27T10:00:00+02:00", "phase": "Activity", "actor": "Analyst",
            "action": "Neuer Schritt", "status": "open"}
    data.update(changes)
    return Event.model_validate(data)


def test_a_refused_event_creation_changes_nothing(folder, conn):
    break_folder(folder)
    before = snapshot(folder)

    with pytest.raises(Invalid):
        repository.create_event(conn, new_event(), ["inc-2026-042"], actor_id=None, ip="test", data_dir=folder)

    assert snapshot(folder) == before
    assert repository.get_event(conn, "neu-1") is None


def test_a_failed_link_takes_the_new_event_back_with_it(folder, conn):
    before = snapshot(folder)

    with pytest.raises(NotFound):
        repository.create_event(conn, new_event(), ["inc-2026-042", "gibt-es-nicht"], actor_id=None, ip="test",
                                data_dir=folder)

    assert snapshot(folder) == before
    assert repository.get_event(conn, "neu-1") is None


def test_a_refused_deletion_keeps_the_event_and_its_links(folder, conn):
    repository.update_event(conn, "lessons", _event_of(conn, "lessons"), ["inc-2026-042", "b"],
                            repository.get_event(conn, "lessons")["version"], actor_id=None, ip="test",
                            data_dir=folder)
    assert "lessons" in (folder / "b.yaml").read_text(encoding="utf-8")
    break_folder(folder)
    before = snapshot(folder)

    with pytest.raises(Invalid):
        repository.delete_event(conn, "lessons", actor_id=None, ip="test", data_dir=folder)

    assert snapshot(folder) == before
    assert repository.get_event(conn, "lessons") is not None


def test_a_refused_attachment_leaves_no_file_behind(folder, conn):
    break_folder(folder)
    before = snapshot(folder)

    with pytest.raises(Invalid):
        repository.add_evidence(conn, "analysis", check_file("neu.png", _png(9)), "screenshot", "",
                                actor_id=None, ip="test", data_dir=folder)

    assert snapshot(folder) == before


def test_a_refused_attachment_removal_keeps_the_file(folder, conn):
    evidence_id = conn.execute(
        "SELECT evidence.pk FROM evidence JOIN events ON events.pk = evidence.event_pk WHERE events.key = 'siem-alert'"
    ).fetchone()["pk"]
    break_folder(folder)
    before = snapshot(folder)

    with pytest.raises(Invalid):
        repository.delete_evidence(conn, evidence_id, actor_id=None, ip="test", data_dir=folder)

    assert snapshot(folder) == before
    assert (folder / "evidence" / "siem-alert.png").exists()


def _event_of(conn, key):
    current = repository.get_event(conn, key)
    data = {field: current[field] for field in ("key", "t0", "phase", "actor", "system", "action", "status",
                                                "triggered_by", "details")}
    return Event.model_validate(data)


def _png(marker: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + bytes([marker]) * 24


def test_a_failed_change_does_not_take_a_parallel_change_back_out_of_the_folder(folder, conn, monkeypatch):
    """Issue #126: the rollback of a failed change restores every topic file as it was when that change started.

    The first change writes its file and then fails. While it is in that state a second change writes another file.
    The rollback of the first change must not take the second change back out of its file.
    """
    first_is_writing = threading.Event()
    second_finished = threading.Event()
    real_reindex = repository.reindex

    def reindex_that_fails_for_the_first_change(conn_, data_dir, *args, **kwargs):
        if threading.current_thread().name.startswith("erste"):
            first_is_writing.set()
            # Without a lock the second change finishes now. With the lock it waits for this rollback.
            second_finished.wait(timeout=1)
            raise Invalid("absichtlich abgebrochen")
        return real_reindex(conn_, data_dir, *args, **kwargs)

    monkeypatch.setattr(repository, "reindex", reindex_that_fails_for_the_first_change)
    current = repository.get_event(conn, "feed-cve")
    edited = _event_of(conn, "feed-cve").model_copy(update={"action": "Parallel geändert"})

    def first_change():
        first_conn = connect(folder.parent / "chronicle.db")
        try:
            repository.update_event(first_conn, "feed-cve", edited, current["topics"], current["version"],
                                   actor_id=None, ip="test", data_dir=folder)
        finally:
            first_conn.close()

    def second_change():
        first_is_writing.wait(timeout=5)
        second_conn = connect(folder.parent / "chronicle.db")
        try:
            repository.create_event(second_conn, new_event("neu-parallel"), ["b"], actor_id=None, ip="test",
                                    data_dir=folder)
        finally:
            second_conn.close()
            second_finished.set()

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="erste") as first, \
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="zweite") as second:
        failing = first.submit(first_change)
        succeeding = second.submit(second_change)
        with pytest.raises(Invalid):
            failing.result()
        succeeding.result()

    assert "key: neu-parallel" in (folder / "b.yaml").read_text(encoding="utf-8")
    assert repository.get_event(conn, "neu-parallel") is not None
