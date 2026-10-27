"""File mode (ADR 0008, issue #89): creating and deleting events writes the topic file, then the index follows it."""


import pytest

from chronicle import repository
from chronicle.models import Event
from chronicle.repository import Conflict, NotFound


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


@pytest.fixture(autouse=True)
def insecure_cookies_for_http_tests(monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")


def test_api_creates_and_deletes_events_in_the_file(folder, api_client):
    client = api_client()
    body = {"key": "api-neu", "t0": "2026-10-27T10:00:00+02:00", "phase": "Activity", "actor": "Anna",
            "action": "über die API", "status": "open", "topic": "inc-2026-042", "topics": ["inc-2026-042"]}

    created = client.post("/api/v1/events", json=body)
    assert created.status_code == 201, created.text
    assert "key: api-neu" in (folder / "timeline.yaml").read_text(encoding="utf-8")

    deleted = client.delete("/api/v1/events/api-neu")
    assert deleted.status_code == 204, deleted.text
    assert "key: api-neu" not in (folder / "timeline.yaml").read_text(encoding="utf-8")
