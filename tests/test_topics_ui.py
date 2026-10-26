import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chronicle import accounts, security
from chronicle.api import create_app
from chronicle.db import connect
from chronicle.importer import import_folder

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def insecure_cookies(monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "topics.db"
    conn = connect(path)
    accounts.create_user(conn, "anna", security.hash_password(PASSWORD), "analyst")
    accounts.create_user(conn, "viewer", security.hash_password(PASSWORD), "viewer")
    conn.close()
    import_folder(path, DATA_DIR)
    return path


def api_login(db_path, username):
    client = TestClient(create_app(db_path))
    response = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client


def topic_body(key="projekt-x", **overrides):
    body = {"key": key, "title": "Projekt X", "kind": "project", "summary": "Härtung"}
    body.update(overrides)
    return body


# Acceptance: an analyst creates a topic and links events, without touching YAML.

def test_analyst_creates_a_topic_and_links_events_in_the_ui(db_path):
    anna = api_login(db_path, "anna")
    csrf = anna.headers["X-CSRF-Token"]

    created = anna.post(
        "/topics/new",
        data={"csrf_token": csrf, "title": "Projekt X", "kind": "project", "summary": "Härtung"},
        follow_redirects=False,
    )
    assert created.status_code == 303
    location = created.headers["location"]
    key = location.rsplit("/", 1)[-1]
    assert re.fullmatch(r"tp-\d{8}-[0-9a-f]{8}", key)  # the UI assigns the key

    linked = anna.post(f"/topics/{key}/link", data={"csrf_token": csrf, "event": "mitigation"}, follow_redirects=False)
    assert linked.status_code == 303
    assert linked.headers["location"] == location

    events = anna.get(f"/api/v1/topics/{key}/events").json()
    assert [event["key"] for event in events] == ["mitigation"]
    assert "Projekt X" in anna.get(location).text


def test_ui_ignores_a_key_in_the_form_and_the_form_does_not_ask_for_one(db_path):
    anna = api_login(db_path, "anna")
    assert 'name="key"' not in anna.get("/topics/new").text
    created = anna.post(
        "/topics/new",
        data={"csrf_token": anna.headers["X-CSRF-Token"], "key": "mein-key", "title": "X", "kind": "project"},
        follow_redirects=False,
    )
    assert created.headers["location"] != "/topics/mein-key" and created.headers["location"].startswith("/topics/tp-")
    assert anna.get("/api/v1/topics/mein-key").status_code == 404


def test_api_generates_a_key_when_none_is_given_and_keeps_a_given_one(db_path):
    anna = api_login(db_path, "anna")
    generated = anna.post("/api/v1/topics", json={"title": "Ohne Key", "kind": "project"})
    assert generated.status_code == 201 and re.fullmatch(r"tp-\d{8}-[0-9a-f]{8}", generated.json()["key"])
    assert anna.post("/api/v1/topics", json=topic_body()).json()["key"] == "projekt-x"
    assert anna.post("/api/v1/topics", json={"title": "Y", "key": "mit leerzeichen"}).status_code == 422


# Roles and rules

def test_viewer_cannot_create_link_or_delete(db_path):
    viewer = api_login(db_path, "viewer")
    assert viewer.post("/api/v1/topics", json=topic_body()).status_code == 403
    assert viewer.post("/api/v1/topics/inc-2026-042/events", json={"event": "lessons"}).status_code == 403
    assert viewer.delete("/api/v1/topics/inc-2026-042").status_code == 403
    assert viewer.get("/topics/new", follow_redirects=False).status_code == 403
    assert viewer.get("/topics").status_code == 200  # reading is open to everybody


def test_topic_key_is_unique_and_validated(db_path):
    anna = api_login(db_path, "anna")
    assert anna.post("/api/v1/topics", json=topic_body()).status_code == 201
    assert anna.post("/api/v1/topics", json=topic_body()).status_code == 409
    assert anna.post("/api/v1/topics", json=topic_body(key="mit leerzeichen")).status_code == 422
    assert anna.post("/api/v1/topics", json=topic_body(key="x-2", kind="unbekannt")).status_code == 422


def test_title_can_change_but_key_cannot(db_path):
    anna = api_login(db_path, "anna")
    anna.post("/api/v1/topics", json=topic_body())

    updated = anna.put("/api/v1/topics/projekt-x", json=topic_body(title="Neuer Titel"))
    assert updated.status_code == 200
    assert updated.json()["title"] == "Neuer Titel"

    moved = anna.put("/api/v1/topics/projekt-x", json=topic_body(key="anderer-key"))
    assert moved.status_code == 422


def test_topic_with_home_events_cannot_be_deleted(db_path):
    anna = api_login(db_path, "anna")
    response = anna.delete("/api/v1/topics/inc-2026-042")
    assert response.status_code == 409
    assert "feed-cve" in response.json()["detail"]


def test_empty_topic_can_be_deleted(db_path):
    anna = api_login(db_path, "anna")
    anna.post("/api/v1/topics", json=topic_body())
    assert anna.delete("/api/v1/topics/projekt-x").status_code == 204
    assert anna.get("/api/v1/topics/projekt-x").status_code == 404


def test_home_link_of_an_event_cannot_be_removed(db_path):
    anna = api_login(db_path, "anna")
    response = anna.delete("/api/v1/topics/inc-2026-042/events/exploit")
    assert response.status_code == 422


def test_referenced_link_can_be_removed_and_the_event_stays(db_path):
    anna = api_login(db_path, "anna")
    anna.post("/api/v1/topics", json=topic_body())
    anna.post("/api/v1/topics/projekt-x/events", json={"event": "mitigation"})

    assert anna.delete("/api/v1/topics/projekt-x/events/mitigation").status_code == 204
    assert anna.get("/api/v1/events/mitigation").status_code == 200
    assert anna.get("/api/v1/topics/projekt-x/events").json() == []


def test_linking_an_unknown_event_is_not_found(db_path):
    anna = api_login(db_path, "anna")
    assert anna.post("/api/v1/topics/inc-2026-042/events", json={"event": "gibt-es-nicht"}).status_code == 404


def test_html_forms_need_the_csrf_token(db_path):
    anna = api_login(db_path, "anna")
    response = anna.post(
        "/topics/new",
        data={"csrf_token": "falsch", "title": "X", "kind": "project"},
        follow_redirects=False,
    )
    assert response.status_code == 403


def test_topic_changes_are_audited(db_path):
    anna = api_login(db_path, "anna")
    anna.post("/api/v1/topics", json=topic_body())
    anna.put("/api/v1/topics/projekt-x", json=topic_body(title="Neu"))
    anna.post("/api/v1/topics/projekt-x/events", json={"event": "mitigation"})

    conn = connect(db_path)
    actions = {entry["action"] for entry in accounts.list_audit(conn)}
    conn.close()
    assert {"topic_created", "topic_updated", "topic_linked"} <= actions


def test_html_form_shows_validation_errors(db_path):
    anna = api_login(db_path, "anna")
    response = anna.post(
        "/topics/new",
        data={"csrf_token": anna.headers["X-CSRF-Token"], "title": "", "kind": "project"},
        follow_redirects=False,
    )
    assert response.status_code == 422
    assert "title" in response.text
