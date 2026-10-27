import pytest
from fastapi.testclient import TestClient

from tests.conftest import app_for
from chronicle import accounts, security
from chronicle.db import connect

ADMIN_USER = "ci-admin"
FIRST_PASSWORD = "erstpasswort-123"
NEW_PASSWORD = "eigenes-passwort-456"


@pytest.fixture(autouse=True)
def insecure_cookies(monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "password.db"
    conn = connect(path)
    accounts.create_user(conn, "anna", security.hash_password("correct horse battery"), "analyst")
    conn.close()
    return path


def login_api(db_path, username, password):
    client = TestClient(app_for(db_path))
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    return client, response


def logged_in(db_path, username, password):
    client, response = login_api(db_path, username, password)
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client, response.json()


def test_bootstrap_admin_must_change_the_password_first(tmp_path, monkeypatch):
    path = tmp_path / "bootstrap.db"
    monkeypatch.setenv("CHRONICLE_ADMIN_USER", ADMIN_USER)
    monkeypatch.setenv("CHRONICLE_ADMIN_PASSWORD", FIRST_PASSWORD)
    app_for(path)  # the bootstrap admin is created when the app starts

    client, response = login_api(path, ADMIN_USER, FIRST_PASSWORD)
    assert response.json()["user"]["must_change_password"] is True
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]

    assert client.get("/api/v1/users").status_code == 403  # admin functions are closed
    assert client.get("/api/v1/auth/me").status_code == 200  # but the change is possible


def test_password_change_opens_the_account(db_path):
    accounts_conn = connect(db_path)
    accounts.create_user(accounts_conn, "frisch", security.hash_password(FIRST_PASSWORD), "analyst", must_change=True)
    accounts_conn.close()

    client, _ = logged_in(db_path, "frisch", FIRST_PASSWORD)
    assert client.get("/api/v1/events").status_code == 403
    assert client.post("/api/v1/auth/password", json={"current_password": FIRST_PASSWORD, "new_password": NEW_PASSWORD}).status_code == 204

    assert client.get("/api/v1/events").status_code == 200
    assert login_api(db_path, "frisch", FIRST_PASSWORD)[1].status_code == 401
    assert login_api(db_path, "frisch", NEW_PASSWORD)[1].json()["user"]["must_change_password"] is False


def test_wrong_current_password_is_refused(db_path):
    client, _ = logged_in(db_path, "anna", "correct horse battery")
    response = client.post("/api/v1/auth/password", json={"current_password": "falsch", "new_password": NEW_PASSWORD})
    assert response.status_code == 403


def test_new_password_must_follow_the_policy_and_differ(db_path):
    client, _ = logged_in(db_path, "anna", "correct horse battery")
    too_short = client.post("/api/v1/auth/password", json={"current_password": "correct horse battery", "new_password": "kurz"})
    same = client.post(
        "/api/v1/auth/password", json={"current_password": "correct horse battery", "new_password": "correct horse battery"}
    )
    assert too_short.status_code == 422
    assert same.status_code == 422


def test_change_ends_other_sessions_and_keeps_this_one(db_path):
    here, _ = logged_in(db_path, "anna", "correct horse battery")
    elsewhere, _ = logged_in(db_path, "anna", "correct horse battery")

    assert here.post("/api/v1/auth/password", json={"current_password": "correct horse battery", "new_password": NEW_PASSWORD}).status_code == 204

    assert here.get("/api/v1/auth/me").status_code == 200
    assert elsewhere.get("/api/v1/auth/me").status_code == 401


def test_admin_reset_forces_a_new_password_and_ends_sessions(db_path):
    admin_conn = connect(db_path)
    accounts.create_user(admin_conn, "chef", security.hash_password("chef-passwort-789"), "admin")
    admin_conn.close()
    admin, _ = logged_in(db_path, "chef", "chef-passwort-789")
    anna_id = accounts.get_user_by_name(connect(db_path), "anna")["id"]

    anna_session, _ = logged_in(db_path, "anna", "correct horse battery")
    reset = admin.patch(f"/api/v1/users/{anna_id}", json={"password": FIRST_PASSWORD})
    assert reset.status_code == 200

    assert anna_session.get("/api/v1/auth/me").status_code == 401
    _, body = logged_in(db_path, "anna", FIRST_PASSWORD)
    assert body["user"]["must_change_password"] is True


def test_user_created_by_admin_must_change_the_password(db_path):
    admin_conn = connect(db_path)
    accounts.create_user(admin_conn, "chef", security.hash_password("chef-passwort-789"), "admin")
    admin_conn.close()
    admin, _ = logged_in(db_path, "chef", "chef-passwort-789")

    created = admin.post("/api/v1/users", json={"username": "neu-angelegt", "password": FIRST_PASSWORD, "role": "viewer"})
    assert created.status_code == 201
    _, body = logged_in(db_path, "neu-angelegt", FIRST_PASSWORD)
    assert body["user"]["must_change_password"] is True


def test_pages_redirect_to_the_change_form(db_path):
    conn = connect(db_path)
    accounts.create_user(conn, "frisch", security.hash_password(FIRST_PASSWORD), "analyst", must_change=True)
    conn.close()
    client, _ = logged_in(db_path, "frisch", FIRST_PASSWORD)

    for path in ("/", "/events", "/events/new"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303, path
        assert response.headers["location"] == "/account/password", path

    page = client.get("/account/password")
    assert page.status_code == 200
    assert "Dein Passwort wurde vergeben" in page.text


def test_password_form_changes_the_password(db_path):
    client, _ = logged_in(db_path, "anna", "correct horse battery")
    csrf = client.get("/api/v1/auth/me").json()["csrf_token"]

    wrong = client.post(
        "/account/password",
        data={"current_password": "falsch", "new_password": NEW_PASSWORD, "csrf_token": csrf},
        follow_redirects=False,
    )
    assert wrong.status_code == 403

    changed = client.post(
        "/account/password",
        data={"current_password": "correct horse battery", "new_password": NEW_PASSWORD, "csrf_token": csrf},
        follow_redirects=False,
    )
    assert changed.status_code == 303
    assert changed.headers["location"] == "/"
    assert login_api(db_path, "anna", NEW_PASSWORD)[1].status_code == 200


def test_password_form_rejects_a_missing_csrf_token(db_path):
    client, _ = logged_in(db_path, "anna", "correct horse battery")
    response = client.post(
        "/account/password",
        data={"current_password": "correct horse battery", "new_password": NEW_PASSWORD, "csrf_token": "falsch"},
        follow_redirects=False,
    )
    assert response.status_code == 403


def test_password_change_is_audited_without_the_password(db_path):
    client, _ = logged_in(db_path, "anna", "correct horse battery")
    client.post("/api/v1/auth/password", json={"current_password": "correct horse battery", "new_password": NEW_PASSWORD})

    conn = connect(db_path)
    entries = [entry for entry in accounts.list_audit(conn) if entry["action"] == "password_changed"]
    conn.close()
    assert len(entries) == 1
    assert NEW_PASSWORD not in repr(entries[0])
