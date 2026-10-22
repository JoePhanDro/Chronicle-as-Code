"""Search page with separate hits for events, actors and systems; help page; system filter; settings markup (issue #12)."""

import pytest
from fastapi.testclient import TestClient

from chronicle import accounts, security
from chronicle.api import create_app
from chronicle.db import connect
from chronicle.importer import import_folder

PASSWORD = "correct horse battery"

TIMELINE = """
topic:
  key: inc-suche
  title: "INC: Suche"
  kind: incident
events:
  - key: ev-webshell
    t0: 2026-10-14T08:40:00+02:00
    phase: Exploit
    actor: Angreifer (extern)
    system: wp-portal-01
    action: "Webshell im Upload-Verzeichnis"
    status: open
  - key: ev-analyse
    t0: 2026-10-14T10:00:00+02:00
    phase: Analysis
    actor: CSIRT Analyst A
    system: siem-core
    action: "Logs ausgewertet"
    status: resolved
  - key: ev-ohne-system
    t0: 2026-10-14T11:00:00+02:00
    phase: Lessons Learned
    actor: CSIRT Lead
    action: "Nachbereitung"
    status: resolved
"""


@pytest.fixture(autouse=True)
def insecure_cookies(monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "search.db"
    conn = connect(path)
    accounts.create_user(conn, "anna", security.hash_password(PASSWORD), "analyst")
    accounts.create_user(conn, "viewer", security.hash_password(PASSWORD), "viewer")
    conn.close()
    data = tmp_path / "data"
    data.mkdir()
    (data / "timeline.yaml").write_text(TIMELINE, encoding="utf-8")
    import_folder(path, data)
    return path


def login(db_path, username):
    client = TestClient(create_app(db_path))
    response = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return client


def test_search_groups_hits_into_events_actors_and_systems(db_path):
    html = login(db_path, "viewer").get("/suche", params={"q": "analyst"}).text

    assert "Logs ausgewertet" in html            # event whose actor matches
    assert 'href="/events?actor=CSIRT+Analyst+A"' in html
    assert 'href="/events?system=siem-core"' not in html  # system does not match "analyst"


def test_search_finds_systems_and_links_to_their_events(db_path):
    html = login(db_path, "viewer").get("/suche", params={"q": "siem"}).text

    assert "Systeme" in html
    assert 'href="/events?system=siem-core"' in html


def test_search_without_text_explains_what_is_searched(db_path):
    html = login(db_path, "viewer").get("/suche").text

    assert "Gib einen Suchbegriff ein" in html
    assert 'id="hits-actors"' not in html


def test_search_needs_a_session(db_path):
    client = TestClient(create_app(db_path))
    response = client.get("/suche", params={"q": "webshell"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_events_page_filters_by_system(db_path):
    html = login(db_path, "viewer").get("/events", params={"system": "wp-portal"}).text

    assert "Webshell im Upload-Verzeichnis" in html
    assert "Logs ausgewertet" not in html
    assert "im System" in html


def test_help_page_lists_shortcuts_and_settings(db_path):
    response = login(db_path, "viewer").get("/hilfe")

    assert response.status_code == 200
    assert "Tastenkürzel" in response.text
    assert "Zeitzone" in response.text


def test_help_page_needs_a_session(db_path):
    client = TestClient(create_app(db_path))
    response = client.get("/hilfe", follow_redirects=False)
    assert response.status_code == 303


def test_settings_dialog_offers_zone_graph_and_timeline_options(db_path):
    html = login(db_path, "viewer").get("/events").text

    for setting in [
        "chronicle.zone", "chronicle.graph.physics", "chronicle.graph.labels",
        "chronicle.timeline.zoom", "chronicle.timeline.group",
    ]:
        assert f'data-setting="{setting}"' in html, setting
    assert 'href="/hilfe"' in html


def test_event_times_carry_their_utc_value_for_the_zone_setting(db_path):
    html = login(db_path, "viewer").get("/events").text

    assert 'data-utc="2026-10-14T06:40:00+00:00"' in html
    assert "data-zone-label" in html
