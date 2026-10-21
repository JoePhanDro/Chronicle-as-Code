import re

import pytest
from fastapi.testclient import TestClient

from chronicle import accounts, security
from chronicle.api import create_app
from chronicle.db import connect

PASSWORD = "erster-admin-passwort"
CODE_LINE = re.compile(r"Einmal-Code für /setup: (\S+)")


@pytest.fixture(autouse=True)
def insecure_cookies(monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")
    monkeypatch.delenv("CHRONICLE_ADMIN_USER", raising=False)
    monkeypatch.delenv("CHRONICLE_ADMIN_PASSWORD", raising=False)


def start(db_path, capsys, app=None):
    """Start the app like the server does and return the client and the one-time code from the log."""
    client = TestClient(app or create_app(db_path))
    client.__enter__()  # runs the startup hook, which prints the code
    match = CODE_LINE.search(capsys.readouterr().err)
    return client, (match.group(1) if match else None)


def form_csrf(client):
    response = client.get("/setup")
    return re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)


def user_count(db_path):
    conn = connect(db_path)
    try:
        return accounts.count_users(conn)
    finally:
        conn.close()


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "setup.db"


def test_empty_database_sends_visitors_to_the_setup_page(db_path, capsys):
    client, code = start(db_path, capsys)
    assert code is not None
    assert client.get("/login", follow_redirects=False).headers["location"] == "/setup"
    assert client.get("/setup").status_code == 200
    client.__exit__(None, None, None)


def test_the_code_is_printed_once_per_start(db_path, capsys):
    client, first = start(db_path, capsys)
    client.__exit__(None, None, None)
    client, second = start(db_path, capsys)
    client.__exit__(None, None, None)
    assert first and second and first != second  # a new start, a new code


def test_wrong_code_creates_nothing(db_path, capsys):
    client, _ = start(db_path, capsys)
    csrf = form_csrf(client)
    response = client.post(
        "/setup",
        data={"csrf_token": csrf, "setup_code": "falsch", "username": "chef", "password": PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 403
    assert user_count(db_path) == 0
    client.__exit__(None, None, None)


def test_form_without_the_setup_cookie_is_refused(db_path, capsys):
    app = create_app(db_path)
    client, code = start(db_path, capsys, app=app)
    csrf = form_csrf(client)
    stranger = TestClient(app)  # same server, but no setup cookie
    response = stranger.post(
        "/setup",
        data={"csrf_token": csrf, "setup_code": code, "username": "chef", "password": PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 403
    assert user_count(db_path) == 0
    client.__exit__(None, None, None)


def test_correct_code_creates_the_admin_and_signs_in(db_path, capsys):
    client, code = start(db_path, capsys)
    csrf = form_csrf(client)
    response = client.post(
        "/setup",
        data={"csrf_token": csrf, "setup_code": code, "username": "chef", "password": PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["role"] == "admin"
    assert client.get("/api/v1/users").status_code == 200  # the first admin can manage accounts

    conn = connect(db_path)
    row = accounts.get_user_by_name(conn, "chef")
    assert row["must_change_password"] == 0  # the admin chose this password, no forced change
    assert security.verify_password(row["password_hash"], PASSWORD)
    conn.close()
    client.__exit__(None, None, None)


def test_setup_closes_after_the_first_account(db_path, capsys):
    client, code = start(db_path, capsys)
    csrf = form_csrf(client)
    client.post("/setup", data={"csrf_token": csrf, "setup_code": code, "username": "chef", "password": PASSWORD},
                follow_redirects=False)
    client.cookies.clear()

    assert client.get("/setup", follow_redirects=False).headers["location"] == "/login"
    again = client.post(
        "/setup",
        data={"csrf_token": csrf, "setup_code": code, "username": "zweiter", "password": PASSWORD},
        follow_redirects=False,
    )
    assert again.headers["location"] == "/login"
    assert user_count(db_path) == 1
    client.__exit__(None, None, None)


def test_weak_password_keeps_the_code_valid(db_path, capsys):
    client, code = start(db_path, capsys)
    csrf = form_csrf(client)
    weak = client.post("/setup", data={"csrf_token": csrf, "setup_code": code, "username": "chef", "password": "kurz"},
                       follow_redirects=False)
    assert weak.status_code == 422
    assert user_count(db_path) == 0

    # The form comes back with a fresh token, as in a browser.
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', weak.text).group(1)
    retry = client.post("/setup", data={"csrf_token": csrf, "setup_code": code, "username": "chef", "password": PASSWORD},
                        follow_redirects=False)
    assert retry.status_code == 303
    client.__exit__(None, None, None)


def test_no_code_when_an_account_already_exists(db_path, capsys):
    conn = connect(db_path)
    accounts.create_user(conn, "anna", security.hash_password(PASSWORD), "analyst")
    conn.close()

    client, code = start(db_path, capsys)
    assert code is None
    assert client.get("/setup", follow_redirects=False).headers["location"] == "/login"
    client.__exit__(None, None, None)


def test_no_code_when_the_admin_comes_from_the_environment(db_path, capsys, monkeypatch):
    monkeypatch.setenv("CHRONICLE_ADMIN_USER", "ci-admin")
    monkeypatch.setenv("CHRONICLE_ADMIN_PASSWORD", PASSWORD)

    client, code = start(db_path, capsys)
    assert code is None
    assert user_count(db_path) == 1
    client.__exit__(None, None, None)


def test_first_account_creation_is_audited(db_path, capsys):
    client, code = start(db_path, capsys)
    csrf = form_csrf(client)
    client.post("/setup", data={"csrf_token": csrf, "setup_code": code, "username": "chef", "password": PASSWORD},
                follow_redirects=False)
    conn = connect(db_path)
    actions = [entry["action"] for entry in accounts.list_audit(conn)]
    conn.close()
    assert "setup_completed" in actions
    client.__exit__(None, None, None)


def wrong_code(client, csrf, username="chef"):
    return client.post(
        "/setup",
        data={"csrf_token": csrf, "setup_code": "falsch", "username": username, "password": PASSWORD},
        follow_redirects=False,
    )


def test_too_many_wrong_codes_block_the_form_even_for_the_right_code(db_path, capsys):
    client, code = start(db_path, capsys)
    for _ in range(10):
        response = wrong_code(client, form_csrf(client))
        assert response.status_code == 403

    blocked = client.post(
        "/setup",
        data={"csrf_token": form_csrf(client), "setup_code": code, "username": "chef", "password": PASSWORD},
        follow_redirects=False,
    )
    assert blocked.status_code == 429
    assert user_count(db_path) == 0
    client.__exit__(None, None, None)


def test_failures_outside_the_window_do_not_block(db_path, capsys):
    client, code = start(db_path, capsys)
    conn = connect(db_path)
    old = accounts.now() - accounts.FAILURE_WINDOW_SECONDS - 60
    conn.executemany("INSERT INTO login_failures (key, ts) VALUES (?, ?)", [("setup", old)] * 10)
    conn.commit()
    conn.close()

    response = client.post(
        "/setup",
        data={"csrf_token": form_csrf(client), "setup_code": code, "username": "chef", "password": PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 303
    client.__exit__(None, None, None)


def test_wrong_codes_are_audited(db_path, capsys):
    client, _ = start(db_path, capsys)
    wrong_code(client, form_csrf(client))
    conn = connect(db_path)
    actions = [entry["action"] for entry in accounts.list_audit(conn)]
    conn.close()
    assert "setup_failed" in actions
    client.__exit__(None, None, None)

