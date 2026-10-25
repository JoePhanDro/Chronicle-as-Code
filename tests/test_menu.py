import re

import pytest
from fastapi.testclient import TestClient

from chronicle import accounts, security
from chronicle.api import create_app
from chronicle.db import connect

ADMIN_PASSWORD = "admin-passwort-123"
PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def insecure_cookies(monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "menu.db"
    conn = connect(path)
    accounts.create_user(conn, "chef", security.hash_password(ADMIN_PASSWORD), "admin")
    accounts.create_user(conn, "anna", security.hash_password(PASSWORD), "analyst")
    accounts.create_user(conn, "viewer", security.hash_password(PASSWORD), "viewer")
    conn.close()
    return path


def login(db_path, username, password=PASSWORD):
    """Logs in through the API once to get a session cookie; the pages then use the same cookie."""
    client = TestClient(create_app(db_path))
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return client, response.json()["csrf_token"]


def menu_links(html, menu_id):
    """Link targets inside one disclosure menu of the header."""
    panel = re.search(rf'<ul id="{menu_id}"[^>]*>(.*?)</ul>', html, re.S)
    return re.findall(r'href="([^"]+)"', panel.group(1)) if panel else None


def test_viewer_sees_views_but_no_creation_or_admin_menu(db_path):
    client, _ = login(db_path, "viewer")
    html = client.get("/events").text

    assert menu_links(html, "menu-views") == ["/events", "/topics"]
    assert menu_links(html, "menu-new") is None
    assert menu_links(html, "menu-admin") is None
    assert 'href="/events/new"' not in html
    assert "Neues Event" not in html


def test_analyst_gets_the_new_menu_but_not_administration(db_path):
    client, _ = login(db_path, "anna")
    html = client.get("/topics").text

    assert menu_links(html, "menu-new") == ["/events/new", "/topics/new"]
    assert menu_links(html, "menu-admin") is None
    assert menu_links(html, "menu-views") == ["/events", "/topics"]


def test_admin_sees_reports_and_administration(db_path):
    client, _ = login(db_path, "chef", ADMIN_PASSWORD)
    html = client.get("/").text

    assert menu_links(html, "menu-views") == ["/events", "/topics", "/reports"]
    assert menu_links(html, "menu-new") == ["/events/new", "/topics/new"]
    assert menu_links(html, "menu-admin") == ["/admin/users", "/admin/grabsteine", "/admin/audit"]


def test_header_is_the_same_on_every_page(db_path):
    client, _ = login(db_path, "anna")
    for path in ["/", "/events", "/topics", "/events/new", "/topics/new", "/account/password"]:
        html = client.get(path).text
        assert 'aria-label="Hauptmenü"' in html, path
        assert menu_links(html, "menu-views") == ["/events", "/topics"], path


def test_analyst_creates_topic_and_event_through_the_forms(db_path):
    client, csrf = login(db_path, "anna")

    topic = client.post("/topics/new", data={
        "csrf_token": csrf, "key": "menu-test", "title": "Menü-Test", "kind": "project", "summary": "",
    }, follow_redirects=False)
    assert topic.status_code == 303, topic.text

    event = client.post("/events/new", data={
        "csrf_token": csrf, "t0_date": "2026-10-20", "t0_hour": "12", "t0_minute": "00",
        "t0_zone": "Europe/Berlin", "phase": "Identification",
        "actor": "Analyst", "system": "", "action": "Erster Eintrag", "status": "open",
        "triggered_by": "", "details": "", "topic": "menu-test", "topics": [],
    }, follow_redirects=False)
    assert event.status_code == 303, event.text
    assert event.headers["location"].startswith("/events/ev-")

    assert "Erster Eintrag" in client.get(event.headers["location"]).text


def test_viewer_cannot_post_creation_forms(db_path):
    client, csrf = login(db_path, "viewer")
    response = client.post("/topics/new", data={
        "csrf_token": csrf, "key": "nope", "title": "Nein", "kind": "project", "summary": "",
    }, follow_redirects=False)
    assert response.status_code == 403


def test_header_offers_search_and_settings_on_every_page(db_path):
    client, _ = login(db_path, "viewer")
    for path in ["/", "/events", "/topics", "/account/password"]:
        html = client.get(path).text
        assert 'role="search"' in html and 'action="/suche"' in html, path
        assert "data-open-settings" in html and 'id="settings-dialog"' in html, path


def test_account_menu_for_small_screens_holds_the_account_controls(db_path):
    client, _ = login(db_path, "viewer")
    html = client.get("/topics").text

    assert menu_links(html, "menu-account") == ["/hilfe", "/account/password"]
    panel = re.search(r'<ul id="menu-account"[^>]*>(.*?)</ul>', html, re.S).group(1)
    assert "data-open-settings" in panel and 'action="/logout"' in panel
    assert html.count("<button type=\"button\" data-open-settings") == 2  # one in the menu, one in the header for wide screens


def test_every_page_has_a_skip_link_to_the_main_content(db_path):
    client, _ = login(db_path, "viewer")
    for path in ["/", "/events", "/topics", "/hilfe"]:
        html = client.get(path).text
        assert 'href="#main"' in html and '<main id="main"' in html, path
