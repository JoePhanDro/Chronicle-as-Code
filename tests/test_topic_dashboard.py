import re

import pytest
from fastapi.testclient import TestClient

from chronicle import accounts, security
from chronicle.api import create_app
from chronicle.db import connect
from chronicle.importer import import_folder

DATA_DIR = "data"
PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def insecure_cookies(monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "dashboard.db"
    conn = connect(path)
    accounts.create_user(conn, "viewer", security.hash_password(PASSWORD), "viewer")
    conn.close()
    import_folder(path, DATA_DIR)
    return path


def login(db_path):
    client = TestClient(create_app(db_path))
    response = client.post("/api/v1/auth/login", json={"username": "viewer", "password": PASSWORD})
    assert response.status_code == 200, response.text
    return client


def test_dashboard_needs_login(db_path):
    client = TestClient(create_app(db_path))
    response = client.get("/topics/inc-2026-042/dashboard", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_dashboard_loads_evidence_through_the_api_without_embedding(db_path):
    client = login(db_path)
    page = client.get("/topics/inc-2026-042/dashboard")
    assert page.status_code == 200
    assert re.search(r"/api/v1/evidence/\d+", page.text)
    assert "base64," not in page.text  # no evidence embedded as data URI
    assert "Berichts-Snapshot" not in page.text
    assert "Chronicle · Ansicht" in page.text


def test_dashboard_of_unknown_topic_is_not_found(db_path):
    assert login(db_path).get("/topics/gibt-es-nicht/dashboard").status_code == 404


def test_topic_page_embeds_the_dashboard(db_path):
    page = login(db_path).get("/topics/inc-2026-042").text
    assert 'src="/topics/inc-2026-042/dashboard"' in page
    assert 'href="/topics/inc-2026-042/dashboard"' in page


def test_report_still_embeds_evidence_and_keeps_its_snapshot_notice(db_path):
    from chronicle.report import make_report

    conn = connect(db_path)
    accounts.create_user(conn, "chef", security.hash_password(PASSWORD), "admin")
    user_id = conn.execute("SELECT id FROM users WHERE username = 'chef'").fetchone()["id"]
    filename, body = make_report(conn, "inc-2026-042", user_id, "127.0.0.1")
    conn.close()
    text = body.decode("utf-8")
    assert filename == "bericht-inc-2026-042.html"
    assert "data:image" in text
    assert "Berichts-Snapshot" in text
