"""File mode (ADR 0008, issue #89): creating and deleting events writes the topic file, then the index follows it."""

import shutil
from pathlib import Path

import pytest

from chronicle import repository
from chronicle.db import connect
from chronicle.importer import import_folder
from chronicle.models import Event
from chronicle.repository import Conflict, NotFound

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture
def folder(tmp_path):
    copy = tmp_path / "data"
    shutil.copytree(DATA_DIR, copy)
    import_folder(tmp_path / "chronicle.db", copy)
    return copy


@pytest.fixture
def conn(folder):
    connection = connect(folder.parent / "chronicle.db")
    yield connection
    connection.close()


def new_event(key="neu-1", **changes):
    data = {"key": key, "t0": "2026-10-27T10:00:00+02:00", "phase": "Activity", "actor": "Analyst",
            "action": "Neuer Schritt", "status": "open"}
    data.update(changes)
    return Event.model_validate(data)


def test_a_new_event_is_appended_to_its_home_topic_file(folder, conn):
    created = repository.create_event(conn, new_event(), ["inc-2026-042"], actor_id=None, ip="test", data_dir=folder)

    text = (folder / "timeline.yaml").read_text(encoding="utf-8")
    assert "key: neu-1" in text
    assert text.count("key: feed-cve") == 1  # the others are untouched
    assert created["version"] == 1
    assert created["home_topic"] == "inc-2026-042"




def test_a_duplicate_key_is_refused_before_the_file_is_touched(folder, conn):
    before = (folder / "timeline.yaml").read_bytes()
    with pytest.raises(Conflict):
        repository.create_event(conn, new_event(key="exploit"), ["inc-2026-042"], actor_id=None, ip="test",
                               data_dir=folder)
    assert (folder / "timeline.yaml").read_bytes() == before


def test_a_new_event_may_be_triggered_by_an_existing_one(folder, conn):
    repository.create_event(conn, new_event(triggered_by=["analysis"]), ["inc-2026-042"], actor_id=None, ip="test",
                            data_dir=folder)
    assert repository.get_event(conn, "neu-1")["triggered_by"] == ["analysis"]


def test_a_deleted_event_leaves_the_file_and_the_index(folder, conn):
    repository.delete_event(conn, "lessons", actor_id=None, ip="test", data_dir=folder)

    assert "key: lessons" not in (folder / "timeline.yaml").read_text(encoding="utf-8")
    with pytest.raises(NotFound):
        repository.delete_event(conn, "lessons", actor_id=None, ip="test", data_dir=folder)
    assert repository.get_event(conn, "lessons") is None
    assert repository.get_event(conn, "exploit") is not None  # the others stay


def test_an_event_another_one_depends_on_cannot_be_deleted(folder, conn):
    repository.create_event(conn, new_event(triggered_by=["analysis"]), ["inc-2026-042"], actor_id=None, ip="test",
                            data_dir=folder)
    with pytest.raises(Conflict, match="neu-1"):
        repository.delete_event(conn, "analysis", actor_id=None, ip="test", data_dir=folder)




def test_an_event_that_exists_only_in_the_database_survives_a_file_mode_edit(folder, conn):
    # Created in database mode, so it is in no file. An edit in file mode rebuilds the index and must keep it.
    repository.create_event(conn, new_event(key="nur-db"), ["inc-2026-042"], actor_id=None, ip="test")
    current = repository.get_event(conn, "analysis")
    event = Event.model_validate({k: current[k] for k in ("key", "t0", "phase", "actor", "system", "action", "status",
                                                          "triggered_by", "details")} | {"action": "geändert"})

    repository.update_event(conn, "analysis", event, current["topics"], current["version"], actor_id=None, ip="test",
                            data_dir=folder)

    assert repository.get_event(conn, "nur-db") is not None




PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def insecure_cookies_for_http_tests(monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")


def test_api_creates_and_deletes_events_in_the_file(tmp_path, folder):
    from fastapi.testclient import TestClient

    from chronicle import accounts, security
    from chronicle.api import create_app

    setup = connect(tmp_path / "chronicle.db")
    accounts.create_user(setup, "anna", security.hash_password(PASSWORD), "analyst")
    setup.close()
    client = TestClient(create_app(tmp_path / "chronicle.db", data_dir=folder))
    response = client.post("/api/v1/auth/login", json={"username": "anna", "password": PASSWORD})
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    body = {"key": "api-neu", "t0": "2026-10-27T10:00:00+02:00", "phase": "Activity", "actor": "Anna",
            "action": "über die API", "status": "open", "topic": "inc-2026-042", "topics": ["inc-2026-042"]}

    created = client.post("/api/v1/events", json=body)
    assert created.status_code == 201, created.text
    assert "key: api-neu" in (folder / "timeline.yaml").read_text(encoding="utf-8")

    deleted = client.delete("/api/v1/events/api-neu")
    assert deleted.status_code == 204, deleted.text
    assert "key: api-neu" not in (folder / "timeline.yaml").read_text(encoding="utf-8")
