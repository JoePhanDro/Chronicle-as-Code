import time

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
    path = tmp_path / "admin.db"
    conn = connect(path)
    accounts.create_user(conn, "chef", security.hash_password(ADMIN_PASSWORD), "admin")
    accounts.create_user(conn, "anna", security.hash_password(PASSWORD), "analyst")
    accounts.create_user(conn, "viewer", security.hash_password(PASSWORD), "viewer")
    conn.close()
    return path


def login(db_path, username, password=None):
    client = TestClient(create_app(db_path))
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password or PASSWORD})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client


def user_id(db_path, username):
    conn = connect(db_path)
    row = accounts.get_user_by_name(conn, username)
    conn.close()
    return row["id"]


def csrf(client):
    return client.headers["X-CSRF-Token"]


# Access

def test_only_admins_see_the_admin_pages(db_path):
    assert login(db_path, "anna").get("/admin/users").status_code == 403
    assert login(db_path, "viewer").get("/admin/audit").status_code == 403
    assert login(db_path, "chef", ADMIN_PASSWORD).get("/admin/users").status_code == 200


def test_anonymous_visitors_are_sent_to_login(db_path):
    client = TestClient(create_app(db_path))
    response = client.get("/admin/users", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_admin_navigation_is_only_shown_to_admins(db_path):
    assert 'href="/admin/users"' in login(db_path, "chef", ADMIN_PASSWORD).get("/events").text
    assert 'href="/admin/users"' not in login(db_path, "anna").get("/events").text


# Accounts

def test_admin_creates_an_account_that_must_change_its_password(db_path):
    chef = login(db_path, "chef", ADMIN_PASSWORD)
    response = chef.post(
        "/admin/users/new",
        data={"csrf_token": csrf(chef), "username": "neu-im-team", "password": "startpasswort-456", "role": "analyst"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    listed = chef.get("/admin/users").text
    assert "neu-im-team" in listed
    assert "muss beim Login geändert werden" in listed


def test_creating_an_account_with_a_taken_name_shows_the_error(db_path):
    chef = login(db_path, "chef", ADMIN_PASSWORD)
    response = chef.post(
        "/admin/users/new",
        data={"csrf_token": csrf(chef), "username": "anna", "password": "startpasswort-456", "role": "viewer"},
        follow_redirects=False,
    )
    assert response.status_code == 409
    assert "bereits vergeben" in response.text


def test_role_and_activation_change_from_the_form(db_path):
    chef = login(db_path, "chef", ADMIN_PASSWORD)
    anna = user_id(db_path, "anna")
    response = chef.post(
        f"/admin/users/{anna}",
        data={"csrf_token": csrf(chef), "role": "viewer"},  # active unchecked: deactivates
        follow_redirects=False,
    )
    assert response.status_code == 303
    conn = connect(db_path)
    row = conn.execute("SELECT role, active FROM users WHERE id = ?", (anna,)).fetchone()
    conn.close()
    assert (row["role"], row["active"]) == ("viewer", 0)


def test_last_active_admin_cannot_be_demoted_in_the_form(db_path):
    chef = login(db_path, "chef", ADMIN_PASSWORD)
    response = chef.post(
        f"/admin/users/{user_id(db_path, 'chef')}",
        data={"csrf_token": csrf(chef), "role": "viewer", "active": "on"},
        follow_redirects=False,
    )
    assert response.status_code == 409
    assert "letzte aktive Admin" in response.text


def test_unchanged_row_does_not_end_sessions(db_path):
    chef = login(db_path, "chef", ADMIN_PASSWORD)
    anna_session = login(db_path, "anna")
    anna = user_id(db_path, "anna")
    chef.post(f"/admin/users/{anna}", data={"csrf_token": csrf(chef), "role": "analyst", "active": "on"},
              follow_redirects=False)
    assert anna_session.get("/api/v1/auth/me").status_code == 200


def test_admin_resets_a_password_and_the_user_must_change_it(db_path):
    chef = login(db_path, "chef", ADMIN_PASSWORD)
    anna = user_id(db_path, "anna")
    anna_session = login(db_path, "anna")

    response = chef.post(
        f"/admin/users/{anna}/password",
        data={"csrf_token": csrf(chef), "password": "neues-startpasswort-789"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert anna_session.get("/api/v1/auth/me").status_code == 401  # sessions ended
    assert login(db_path, "anna", "neues-startpasswort-789").get("/api/v1/auth/me").json()["username"] == "anna"


def test_password_reset_with_a_short_password_is_refused(db_path):
    chef = login(db_path, "chef", ADMIN_PASSWORD)
    response = chef.post(
        f"/admin/users/{user_id(db_path, 'anna')}/password",
        data={"csrf_token": csrf(chef), "password": "kurz"},
        follow_redirects=False,
    )
    assert response.status_code == 422


def test_admin_forms_need_the_csrf_token(db_path):
    chef = login(db_path, "chef", ADMIN_PASSWORD)
    response = chef.post(
        f"/admin/users/{user_id(db_path, 'anna')}",
        data={"csrf_token": "falsch", "role": "viewer"},
        follow_redirects=False,
    )
    assert response.status_code == 403


# Login lock

def test_admin_lifts_the_lock_of_an_account(db_path):
    anna = user_id(db_path, "anna")
    client = TestClient(create_app(db_path))
    for _ in range(5):
        client.post("/api/v1/auth/login", json={"username": "anna", "password": "falsch"})
    assert client.post("/api/v1/auth/login", json={"username": "anna", "password": PASSWORD}).status_code == 429

    chef = login(db_path, "chef", ADMIN_PASSWORD)
    response = chef.post(f"/admin/users/{anna}/unlock", data={"csrf_token": csrf(chef)}, follow_redirects=False)
    assert response.status_code == 303

    assert client.post("/api/v1/auth/login", json={"username": "anna", "password": PASSWORD}).status_code == 200


# Audit log

def table_rows(html):
    return html.split("<tbody", 1)[1].split("</tbody>", 1)[0]


def test_audit_page_filters_by_action_and_actor(db_path):
    chef = login(db_path, "chef", ADMIN_PASSWORD)
    chef.post("/admin/users/new", data={"csrf_token": csrf(chef), "username": "neu-eins", "password": "startpasswort-456", "role": "viewer"},
              follow_redirects=False)
    login(db_path, "anna")  # a login by someone else

    by_action = table_rows(chef.get("/admin/audit?action=user_created").text)
    assert "neu-eins" in by_action
    assert "login" not in by_action

    by_actor = table_rows(chef.get("/admin/audit?actor=anna").text)
    assert "login" in by_actor
    assert "user_created" not in by_actor


def test_audit_page_filters_by_day(db_path):
    chef = login(db_path, "chef", ADMIN_PASSWORD)
    today = time.strftime("%Y-%m-%d", time.gmtime())
    assert "Keine Einträge" not in chef.get(f"/admin/audit?day={today}").text
    assert "Keine Einträge" in chef.get("/admin/audit?day=2100-01-01").text  # "ab Datum": the future has no entries


def test_audit_page_rejects_a_bad_date(db_path):
    chef = login(db_path, "chef", ADMIN_PASSWORD)
    response = chef.get("/admin/audit?day=gestern")
    assert response.status_code == 200
    assert "JJJJ-MM-TT" in response.text


def test_api_audit_takes_the_same_filters(db_path):
    chef = login(db_path, "chef", ADMIN_PASSWORD)
    chef.post("/api/v1/users", json={"username": "neu-api", "password": "startpasswort-456", "role": "viewer"})
    entries = chef.get("/api/v1/audit?action=user_created&actor=chef").json()
    assert [entry["target"] for entry in entries] == ["neu-api"]
    assert chef.get("/api/v1/audit?since=4102444800").json() == []  # nothing after 2100-01-01
