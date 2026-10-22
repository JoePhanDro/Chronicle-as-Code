from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from chronicle import accounts, repository, security
from chronicle.api import create_app
from chronicle.db import connect
from chronicle.importer import import_folder

PASSWORD = "correct horse battery"

EVENT_COUNT = 1000
WITH_EVIDENCE = range(0, EVENT_COUNT, 10)  # every tenth event has one piece of evidence


def write_timeline(folder: Path) -> None:
    (folder / "evidence").mkdir(parents=True)
    (folder / "evidence" / "protokoll.txt").write_text("Protokollauszug\n", encoding="utf-8")
    events = []
    for index in range(EVENT_COUNT):
        event = {
            "key": f"ev-{index:04d}",
            "t0": f"2026-10-14T{index // 60 % 24:02d}:{index % 60:02d}:00+02:00",
            "phase": "Identification",
            "actor": "SIEM",
            "action": f"Alarm {index}",
            "status": "open",
        }
        if index in WITH_EVIDENCE:
            event["evidence"] = [{"file": "evidence/protokoll.txt", "type": "log", "description": f"Beleg {index}"}]
        events.append(event)
    topic = {"key": "last-test", "title": "Lasttest", "kind": "incident"}
    (folder / "timeline.yaml").write_text(yaml.safe_dump({"topic": topic, "events": events}, allow_unicode=True), encoding="utf-8")


def test_listing_many_events_takes_a_fixed_number_of_queries(tmp_path):
    folder = tmp_path / "data"
    write_timeline(folder)
    db_path = tmp_path / "queries.db"
    import_folder(db_path, folder)

    conn = connect(db_path)
    statements = []
    conn.set_trace_callback(statements.append)
    events = repository.list_events(conn)
    conn.set_trace_callback(None)
    conn.close()

    assert len(events) == EVENT_COUNT
    assert len(statements) <= 5, f"{len(statements)} queries for {EVENT_COUNT} events"


def test_evidence_is_attached_to_the_right_events(tmp_path):
    folder = tmp_path / "data"
    write_timeline(folder)
    db_path = tmp_path / "evidence.db"
    import_folder(db_path, folder)

    conn = connect(db_path)
    events = {event["key"]: event for event in repository.list_events(conn)}
    conn.close()

    for index in range(EVENT_COUNT):
        event = events[f"ev-{index:04d}"]
        if index in WITH_EVIDENCE:
            assert [item["description"] for item in event["evidence"]] == [f"Beleg {index}"]
        else:
            assert event["evidence"] == []


@pytest.fixture
def imported_db(tmp_path):
    folder = tmp_path / "data"
    write_timeline(folder)
    db_path = tmp_path / "paging.db"
    import_folder(db_path, folder)
    return db_path


def keys_of(events):
    return [event["key"] for event in events]


def test_limit_and_offset_return_the_matching_slice(imported_db):
    conn = connect(imported_db)
    everything = keys_of(repository.list_events(conn))
    page = keys_of(repository.list_events(conn, limit=10, offset=20))
    tail = keys_of(repository.list_events(conn, limit=5, offset=EVENT_COUNT - 2))
    conn.close()

    assert page == everything[20:30]
    assert tail == everything[-2:]


def test_paging_works_for_one_topic(imported_db):
    conn = connect(imported_db)
    everything = keys_of(repository.list_events(conn, "last-test"))
    page = keys_of(repository.list_events(conn, "last-test", limit=3, offset=10))
    conn.close()

    assert page == everything[10:13]


def test_api_pages_the_event_list(imported_db, monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")
    conn = connect(imported_db)
    accounts.create_user(conn, "viewer", security.hash_password(PASSWORD), "viewer")
    conn.close()

    client = TestClient(create_app(imported_db))
    assert client.post("/api/v1/auth/login", json={"username": "viewer", "password": PASSWORD}).status_code == 200

    everything = client.get("/api/v1/events").json()
    assert len(everything) == EVENT_COUNT  # no parameters: unchanged behaviour
    page = client.get("/api/v1/events", params={"limit": 10, "offset": 20}).json()
    assert keys_of(page) == keys_of(everything)[20:30]


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 501}, {"offset": -1}])
def test_api_rejects_out_of_range_paging(imported_db, monkeypatch, params):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")
    conn = connect(imported_db)
    accounts.create_user(conn, "viewer", security.hash_password(PASSWORD), "viewer")
    conn.close()

    client = TestClient(create_app(imported_db))
    client.post("/api/v1/auth/login", json={"username": "viewer", "password": PASSWORD})
    assert client.get("/api/v1/events", params=params).status_code == 422
