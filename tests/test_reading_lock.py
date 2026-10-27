"""The reading lock (issue #13): with a lock password set, the data reaches visitors without an account only after unlocking."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import app_for, import_example
from chronicle import accounts, security
from chronicle.db import connect

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PASSWORD = "correct horse battery"
ADMIN_PASSWORD = "admin-passwort-123"
LOCK = "sperr-passwort-123"
EVENT_TITLE = "Ungewöhnliche Requests auf /api/upload"


@pytest.fixture(autouse=True)
def insecure_cookies(monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "lock.db"
    conn = connect(path)
    accounts.create_user(conn, "chef", security.hash_password(ADMIN_PASSWORD), "admin")
    accounts.create_user(conn, "anna", security.hash_password(PASSWORD), "analyst")
    conn.close()
    import_example(path)
    return path


def login(db_path, username, password=PASSWORD):
    client = TestClient(app_for(db_path))
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client


def login_csrf(client):
    """The token of the login form. The page sets the matching cookie itself."""
    return re.search(r'name="csrf_token" value="([^"]+)"', client.get("/login").text).group(1)


def unlock(client, password):
    return client.post("/login/entsperren", data={"csrf_token": login_csrf(client), "password": password},
                       follow_redirects=False)


def set_lock(db_path, password=LOCK):
    chef = login(db_path, "chef", ADMIN_PASSWORD)
    return chef.post("/admin/sperre", data={"csrf_token": chef.headers["X-CSRF-Token"], "password": password},
                     follow_redirects=False)


def test_without_a_lock_reading_needs_an_account(db_path):
    client = TestClient(app_for(db_path))
    assert client.get("/events", follow_redirects=False).status_code == 303
    assert "Ansicht entsperren" not in client.get("/login").text


def test_with_a_lock_the_login_page_offers_unlocking_and_shows_no_data(db_path):
    assert set_lock(db_path).status_code == 303

    client = TestClient(app_for(db_path))
    login_page = client.get("/login").text
    assert "Ansicht entsperren" in login_page
    assert EVENT_TITLE not in login_page
    assert client.get("/events", follow_redirects=False).status_code == 303
    assert client.get("/api/v1/events").status_code == 401


def test_wrong_lock_password_keeps_the_data_hidden(db_path):
    set_lock(db_path)
    client = TestClient(app_for(db_path))

    response = unlock(client, "falsch-falsch-1")

    assert response.status_code == 401
    assert "Sperr-Passwort falsch" in response.text
    assert client.get("/events", follow_redirects=False).status_code == 303


def test_correct_lock_password_reads_without_an_account(db_path):
    set_lock(db_path)
    client = TestClient(app_for(db_path))

    response = unlock(client, LOCK)

    assert response.status_code == 303 and response.headers["location"] == "/"
    assert EVENT_TITLE in client.get("/events").text
    assert client.get("/api/v1/events").status_code == 200
    me = client.get("/api/v1/auth/me").json()
    assert me["username"] == accounts.GUEST_USERNAME and me["role"] == "viewer"


def test_reading_session_cannot_change_anything_and_has_no_password_link(db_path):
    set_lock(db_path)
    client = TestClient(app_for(db_path))
    unlock(client, LOCK)
    csrf = client.get("/api/v1/auth/me").json()["csrf_token"]

    response = client.post("/api/v1/topics", json={"key": "x", "title": "X", "kind": "other"},
                           headers={"X-CSRF-Token": csrf})

    assert response.status_code == 403
    assert 'href="/account/password"' not in client.get("/").text


def test_the_guest_account_cannot_log_in_with_a_password(db_path):
    set_lock(db_path)
    client = TestClient(app_for(db_path))

    response = client.post("/api/v1/auth/login", json={"username": accounts.GUEST_USERNAME, "password": LOCK})

    assert response.status_code == 401


def test_repeated_wrong_lock_passwords_are_throttled(db_path):
    set_lock(db_path)
    client = TestClient(app_for(db_path))
    for _ in range(accounts.MAX_FAILED_LOGINS):
        assert unlock(client, "falsch-falsch-1").status_code == 401

    assert unlock(client, LOCK).status_code == 429


def test_changing_the_lock_ends_reading_sessions(db_path):
    set_lock(db_path)
    reader = TestClient(app_for(db_path))
    unlock(reader, LOCK)

    set_lock(db_path, password="neues-sperr-passwort")

    assert reader.get("/events", follow_redirects=False).status_code == 303
    newcomer = TestClient(app_for(db_path))
    assert unlock(newcomer, LOCK).status_code == 401
    assert unlock(newcomer, "neues-sperr-passwort").status_code == 303


def test_clearing_the_lock_needs_accounts_again(db_path):
    set_lock(db_path)
    reader = TestClient(app_for(db_path))
    unlock(reader, LOCK)
    chef = login(db_path, "chef", ADMIN_PASSWORD)

    response = chef.post("/admin/sperre/aufheben", data={"csrf_token": chef.headers["X-CSRF-Token"]},
                         follow_redirects=False)

    assert response.status_code == 303
    assert reader.get("/events", follow_redirects=False).status_code == 303
    client = TestClient(app_for(db_path))
    assert "Ansicht entsperren" not in client.get("/login").text
    assert unlock(client, LOCK).status_code == 404


def test_only_admins_set_the_lock(db_path):
    anna = login(db_path, "anna")

    response = anna.post("/admin/sperre", data={"csrf_token": anna.headers["X-CSRF-Token"], "password": LOCK},
                         follow_redirects=False)

    assert response.status_code == 403


def test_setting_the_lock_needs_the_csrf_token(db_path):
    chef = login(db_path, "chef", ADMIN_PASSWORD)

    response = chef.post("/admin/sperre", data={"csrf_token": "falsch", "password": LOCK}, follow_redirects=False)

    assert response.status_code == 403


def test_short_lock_password_is_refused_on_the_home_page(db_path):
    chef = login(db_path, "chef", ADMIN_PASSWORD)

    response = chef.post("/admin/sperre", data={"csrf_token": chef.headers["X-CSRF-Token"], "password": "kurz"},
                         follow_redirects=False)

    assert response.status_code == 422
    assert "mindestens 12 Zeichen" in response.text


def test_lock_changes_and_unlocks_are_audited(db_path):
    set_lock(db_path)
    unlock(TestClient(app_for(db_path)), "falsch-falsch-1")
    unlock(TestClient(app_for(db_path)), LOCK)

    conn = connect(db_path)
    actions = {row["action"] for row in conn.execute("SELECT action FROM audit_log").fetchall()}
    conn.close()
    assert {"reading_lock_set", "unlock_failed", "unlock"} <= actions
