import sqlite3

import pytest
from fastapi.testclient import TestClient

from chronicle.db import ops_path
from chronicle import accounts, security
from chronicle.api import create_app
from chronicle.db import connect
from chronicle.importer import import_folder
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def insecure_cookies_for_http_tests(monkeypatch):
    # The test client talks plain HTTP, so Secure cookies would not be sent back.
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "chronicle.db"
    conn = connect(path)
    accounts.create_user(conn, "admin", security.hash_password(PASSWORD), "admin")
    accounts.create_user(conn, "anna", security.hash_password(PASSWORD), "analyst")
    accounts.create_user(conn, "viewer", security.hash_password(PASSWORD), "viewer")
    conn.close()
    return path


@pytest.fixture
def client(db_path):
    return TestClient(create_app(db_path))


def login(client, username, password=PASSWORD):
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    return response


def login_as(client, username):
    """Log in and return the CSRF token for later unsafe requests."""
    response = login(client, username)
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def test_topics_require_login(client):
    assert client.get("/api/v1/topics").status_code == 401


def test_health_stays_public(client):
    assert client.get("/api/v1/health").status_code == 200


def test_login_sets_session_and_me_returns_user(client):
    csrf = login_as(client, "anna")
    me = client.get("/api/v1/auth/me").json()
    assert me["username"] == "anna"
    assert me["role"] == "analyst"
    assert me["csrf_token"] == csrf


def test_wrong_password_and_unknown_user_give_the_same_message(client):
    wrong = login(client, "anna", password="falsches passwort!!")
    unknown = login(client, "niemand", password="falsches passwort!!")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_login_is_throttled_after_repeated_failures(client):
    for _ in range(accounts.MAX_FAILED_LOGINS):
        assert login(client, "anna", password="falsch falsch falsch").status_code == 401
    # Even the correct password is refused while the throttle is active.
    assert login(client, "anna").status_code == 429


def test_unsafe_request_without_csrf_token_is_rejected(client):
    login_as(client, "viewer")
    response = client.post("/api/v1/auth/logout")
    assert response.status_code == 403


def test_logout_ends_the_session(client):
    csrf = login_as(client, "anna")
    response = client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 204
    assert client.get("/api/v1/topics").status_code == 401


def test_viewer_cannot_manage_users(client):
    csrf = login_as(client, "viewer")
    assert client.get("/api/v1/users").status_code == 403
    response = client.post(
        "/api/v1/users",
        json={"username": "neu", "password": "langes passwort 123", "role": "viewer"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 403


def test_admin_creates_user_and_duplicate_is_conflict(client):
    csrf = login_as(client, "admin")
    body = {"username": "bob", "password": "langes passwort 123", "role": "viewer"}
    created = client.post("/api/v1/users", json=body, headers={"X-CSRF-Token": csrf})
    assert created.status_code == 201
    assert "password_hash" not in created.json()

    duplicate = client.post("/api/v1/users", json=body, headers={"X-CSRF-Token": csrf})
    assert duplicate.status_code == 409


def test_weak_password_is_rejected(client):
    csrf = login_as(client, "admin")
    response = client.post(
        "/api/v1/users",
        json={"username": "bob", "password": "kurz", "role": "viewer"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 422


def test_password_reset_ends_the_users_sessions(client):
    anna_client = TestClient(client.app)
    login_as(anna_client, "anna")
    assert anna_client.get("/api/v1/topics").status_code == 200

    admin_csrf = login_as(client, "admin")
    response = client.patch(
        "/api/v1/users/2",
        json={"password": "ein neues passwort!"},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert response.status_code == 200
    assert anna_client.get("/api/v1/topics").status_code == 401


def test_deactivated_user_cannot_log_in(client):
    admin_csrf = login_as(client, "admin")
    client.patch("/api/v1/users/2", json={"active": False}, headers={"X-CSRF-Token": admin_csrf})
    assert login(client, "anna").status_code == 401


def test_last_active_admin_cannot_be_demoted(client):
    csrf = login_as(client, "admin")
    response = client.patch("/api/v1/users/1", json={"role": "viewer"}, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 409


def test_expired_session_is_rejected(client, db_path):
    login_as(client, "anna")
    conn = sqlite3.connect(ops_path(db_path))
    conn.execute("UPDATE sessions SET expires_at = 0")
    conn.commit()
    conn.close()
    assert client.get("/api/v1/topics").status_code == 401


def test_passwords_and_tokens_are_not_stored_in_clear(client, db_path):
    login_as(client, "anna")
    conn = sqlite3.connect(ops_path(db_path))
    stored_hashes = [row[0] for row in conn.execute("SELECT password_hash FROM users")]
    token_hashes = [row[0] for row in conn.execute("SELECT token_hash FROM sessions")]
    conn.close()
    assert all(hash_.startswith("$argon2id$") for hash_ in stored_hashes)
    assert PASSWORD not in stored_hashes
    assert all(len(token_hash) == 64 for token_hash in token_hashes)  # SHA-256 hex digests


def test_audit_log_records_logins_and_failures(client):
    login(client, "anna", password="falsch falsch falsch")
    login_as(client, "admin")
    csrf = login_as(client, "admin")
    actions = [entry["action"] for entry in client.get("/api/v1/audit", headers={"X-CSRF-Token": csrf}).json()]
    assert "login_failed" in actions
    assert "login" in actions


def test_html_login_flow(client):
    page = client.get("/login")
    assert page.status_code == 200
    assert 'name="csrf_token"' in page.text

    csrf_form = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    rejected = client.post("/login", data={"username": "anna", "password": PASSWORD, "csrf_token": "falsch"})
    assert rejected.status_code == 403

    accepted = client.post(
        "/login", data={"username": "anna", "password": PASSWORD, "csrf_token": csrf_form}, follow_redirects=False
    )
    assert accepted.status_code == 303
    home = client.get("/")
    assert home.status_code == 200
    assert "anna" in home.text


def test_home_redirects_to_login_without_session(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_import_still_works_and_is_visible_after_login(db_path):
    import_folder(db_path, DATA_DIR)
    client = TestClient(create_app(db_path))
    login_as(client, "viewer")
    assert len(client.get("/api/v1/topics").json()) == 1


def test_bootstrap_admin_from_environment(tmp_path, monkeypatch):
    path = tmp_path / "fresh.db"
    monkeypatch.setenv("CHRONICLE_ADMIN_USER", "root")
    monkeypatch.setenv("CHRONICLE_ADMIN_PASSWORD", PASSWORD)
    create_app(path)
    conn = connect(path)
    assert [user["username"] for user in accounts.list_users(conn)] == ["root"]
    assert accounts.list_users(conn)[0]["role"] == "admin"
    conn.close()
