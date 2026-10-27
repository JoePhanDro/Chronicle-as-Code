from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import app_for, import_example
from chronicle import accounts, security
from chronicle.db import connect

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def insecure_cookies(monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "filter.db"
    conn = connect(path)
    accounts.create_user(conn, "anna", security.hash_password(PASSWORD), "analyst")
    accounts.create_user(conn, "viewer", security.hash_password(PASSWORD), "viewer")
    conn.close()
    import_example(path)

    anna = api_login(path, "anna")
    anna.post("/api/v1/topics", json={"key": "projekt-x", "title": "Projekt X", "kind": "project"})
    anna.post("/api/v1/events", json={
        "key": "projekt-1", "t0": "2026-10-20T09:00:00+02:00", "phase": "Lessons Learned", "actor": "Projekt",
        "action": "Nur im Projekt", "status": "open", "topic": "projekt-x",
    })
    anna.post("/api/v1/topics/projekt-x/events", json={"event": "mitigation"})
    return path


def api_login(db_path, username):
    client = TestClient(app_for(db_path))
    response = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client


def test_filter_shows_home_and_linked_events_only(db_path):
    html = api_login(db_path, "viewer").get("/events?topic=projekt-x").text
    assert "Nur im Projekt" in html  # home event
    assert "Patch eingespielt, Webshell entfernt" in html  # linked event from another topic
    assert "CVE-2026-XXXX veröffentlicht" not in html  # not linked


def test_unfiltered_list_shows_everything(db_path):
    html = api_login(db_path, "viewer").get("/events").text
    assert "Nur im Projekt" in html
    assert "CVE-2026-XXXX veröffentlicht" in html


def test_filter_names_the_topic_and_offers_all_events(db_path):
    html = api_login(db_path, "viewer").get("/events?topic=projekt-x").text
    assert "im Topic" in html
    assert '<option value="projekt-x" selected>' in html
    assert '<option value="">alle</option>' in html


def test_unknown_topic_is_not_found(db_path):
    viewer = api_login(db_path, "viewer")
    assert viewer.get("/events?topic=gibt-es-nicht").status_code == 404


def test_api_filter_matches_the_page(db_path):
    viewer = api_login(db_path, "viewer")
    keys = [event["key"] for event in viewer.get("/api/v1/events?topic=projekt-x").json()]
    assert keys == [event["key"] for event in viewer.get("/api/v1/topics/projekt-x/events").json()]
    assert "projekt-1" in keys and "mitigation" in keys and "feed-cve" not in keys


def test_api_filter_with_unknown_topic_is_not_found(db_path):
    assert api_login(db_path, "viewer").get("/api/v1/events?topic=gibt-es-nicht").status_code == 404


def test_anonymous_request_is_redirected_to_login(db_path):
    client = TestClient(app_for(db_path))
    response = client.get("/events?topic=projekt-x", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_topic_page_links_to_the_filtered_log(db_path):
    html = api_login(db_path, "viewer").get("/topics/projekt-x").text
    assert 'href="/events?topic=projekt-x"' in html
