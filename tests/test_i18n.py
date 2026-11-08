"""Sprache der Oberfläche umschaltbar de/en (issue #220): Cookie chronicle.lang pro Betrachter,
Accept-Language als Fallback, fehlende Übersetzungen fallen auf Deutsch zurück. Berichte bleiben deutsch.
"""

import pytest
from fastapi.testclient import TestClient

from chronicle import accounts, i18n, security
from chronicle.db import connect
from tests.conftest import app_for, import_example

PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def insecure_cookies(monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "i18n.db"
    conn = connect(path)
    accounts.create_user(conn, "anna", security.hash_password(PASSWORD), "analyst")
    conn.close()
    import_example(path)
    return path


def login(db_path, username="anna", **client_kwargs):
    client = TestClient(app_for(db_path), **client_kwargs)
    response = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client


def test_default_is_german(db_path):
    client = login(db_path)
    page = client.get("/events")
    assert page.status_code == 200
    assert '<html lang="de">' in page.text
    assert "Ereignisse" in page.text and "Einstellungen" in page.text


def test_cookie_switches_the_interface_to_english(db_path):
    client = login(db_path)
    client.cookies.set(i18n.LANG_COOKIE, "en")
    page = client.get("/events")
    assert '<html lang="en">' in page.text
    assert ">Events</h1>" in page.text and ">Settings</" in page.text
    assert ">Ereignisse</h1>" not in page.text
    # the settings dialog offers the language choice
    assert 'name="lang"' in page.text and 'value="en"' in page.text and "English" in page.text


def test_accept_language_english_without_cookie(db_path):
    client = login(db_path, headers={"Accept-Language": "en-US,en;q=0.9"})
    assert ">Events</" in client.get("/topics").text


def test_cookie_wins_over_accept_language(db_path):
    client = login(db_path, headers={"Accept-Language": "en-US,en;q=0.9"})
    client.cookies.set(i18n.LANG_COOKIE, "de")
    assert '<html lang="de">' in client.get("/events").text


def test_unknown_cookie_value_falls_back_to_accept_language(db_path):
    client = login(db_path, headers={"Accept-Language": "en"})
    client.cookies.set(i18n.LANG_COOKIE, "fr")
    assert ">Events</" in client.get("/events").text


def test_missing_translation_falls_back_to_german():
    token = i18n.current_lang.set("en")
    try:
        assert i18n.t("Ein noch nicht übersetzter Text") == "Ein noch nicht übersetzter Text"
        assert i18n.t("Ereignisse") == "Events"
    finally:
        i18n.current_lang.reset(token)
    assert i18n.t("Ereignisse") == "Ereignisse"


def test_login_page_switches_too(db_path):
    client = TestClient(app_for(db_path))
    client.cookies.set(i18n.LANG_COOKIE, "en")
    page = client.get("/login")
    assert '<html lang="en">' in page.text and ">Sign in</" in page.text


def test_dashboard_view_switches_but_reports_stay_german(db_path):
    client = login(db_path)
    client.cookies.set(i18n.LANG_COOKIE, "en")
    dashboard = client.get("/topics/inc-2026-042/dashboard?embed=1")
    assert dashboard.status_code == 200
    assert "No event selected" in dashboard.text  # dashboard JS strings switch

    # the report is a document and stays German (reports page is admin-only)
    conn = connect(db_path)
    accounts.create_user(conn, "chef", security.hash_password(PASSWORD), "admin")
    conn.close()
    admin = login(db_path, "chef")
    admin.cookies.set(i18n.LANG_COOKIE, "en")
    report = admin.get("/reports/inc-2026-042")
    assert report.status_code == 200
    assert "Erkannt durch:" in report.text
