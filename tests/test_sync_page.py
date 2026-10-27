"""The Abgleichen page (issue #90): the preview, the button, and what the page shows after a run."""

import shutil

import pytest

from chronicle import accounts, security
from chronicle.api import create_app
from chronicle.db import connect
from tests.conftest import PASSWORD
from tests.gitutil import clone_of, edit, git, remote_of

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git ist nicht installiert")

FEED = 'action: "CVE-2026-XXXX veröffentlicht"'


@pytest.fixture(autouse=True)
def plain_http(monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")


def client_for(tmp_path, folder, role="analyst"):
    from fastapi.testclient import TestClient

    conn = connect(tmp_path / "chronicle.db")
    accounts.create_user(conn, "anna", security.hash_password(PASSWORD), role)
    conn.close()
    client = TestClient(create_app(tmp_path / "chronicle.db", data_dir=folder))
    response = client.post("/api/v1/auth/login", json={"username": "anna", "password": PASSWORD})
    assert response.status_code == 200, response.text
    client.csrf = response.json()["csrf_token"]
    return client


def test_the_page_shows_the_preview_and_the_button(tmp_path, git_folder):
    edit(git_folder, "timeline.yaml", FEED, 'action: "Neu formuliert"')
    client = client_for(tmp_path, git_folder)

    response = client.get("/abgleich")

    assert response.status_code == 200
    assert "geändert" in response.text and "feed-cve" in response.text
    assert 'action="/abgleich"' in response.text


def test_without_a_login_the_page_sends_to_the_login(tmp_path, git_folder):
    from fastapi.testclient import TestClient

    visitor = TestClient(create_app(tmp_path / "chronicle.db", data_dir=git_folder))
    assert visitor.get("/abgleich", follow_redirects=False).headers["location"] == "/login"
    assert visitor.post("/abgleich", follow_redirects=False).headers["location"] == "/login"


def test_a_viewer_is_refused(tmp_path, git_folder):
    client = client_for(tmp_path, git_folder, role="viewer")

    assert client.get("/abgleich").status_code == 403
    assert client.post("/abgleich", data={"csrf_token": client.csrf}).status_code == 403


def test_running_the_sync_pushes_and_says_so(tmp_path, git_folder):
    remote = remote_of(tmp_path, git_folder)
    edit(git_folder, "timeline.yaml", FEED, 'action: "Neu"')
    client = client_for(tmp_path, git_folder)

    response = client.post("/abgleich", data={"csrf_token": client.csrf})

    assert response.status_code == 200
    assert "Abgeglichen." in response.text
    assert "Lokale Änderungen sind committet." in response.text
    assert git(remote, "log", "-1", "--format=%s", "main").startswith("Abgleich ")


def test_the_run_is_refused_without_the_csrf_token(tmp_path, git_folder):
    client = client_for(tmp_path, git_folder)

    assert client.post("/abgleich", data={"csrf_token": "falsch"}).status_code == 403


def test_a_remote_that_cannot_be_reached_is_an_error_on_the_page(tmp_path, git_folder):
    client = client_for(tmp_path, git_folder)  # the folder has no remote

    response = client.post("/abgleich", data={"csrf_token": client.csrf})

    assert response.status_code == 409 and "Der Remote ist nicht erreichbar" in response.text


def test_a_conflict_is_shown_and_nothing_is_pushed(tmp_path, git_folder):
    remote = remote_of(tmp_path, git_folder)
    other = clone_of(tmp_path, remote, "anna")
    edit(other, "timeline.yaml", FEED, 'action: "Von anderswo"')
    git(other, "commit", "-q", "-am", "Anders")
    git(other, "push", "-q", "origin", "main")
    edit(git_folder, "timeline.yaml", FEED, 'action: "Hier"')
    client = client_for(tmp_path, git_folder)

    response = client.post("/abgleich", data={"csrf_token": client.csrf})

    assert response.status_code == 200
    assert "Konflikt in timeline.yaml" in response.text
    assert git(remote, "log", "-1", "--format=%s", "main").startswith("Anders")


def test_the_menu_offers_the_page_to_writers_only(tmp_path, git_folder):
    writer = client_for(tmp_path, git_folder)
    assert 'href="/abgleich"' in writer.get("/events").text

    viewer_dir = tmp_path / "viewer"
    viewer_dir.mkdir()
    viewer = client_for(viewer_dir, git_folder, role="viewer")
    assert 'href="/abgleich"' not in viewer.get("/events").text


def test_a_run_is_recorded_in_the_audit_log_from_the_page_and_from_the_api(tmp_path, git_folder):
    remote_of(tmp_path, git_folder)
    client = client_for(tmp_path, git_folder)
    client.post("/abgleich", data={"csrf_token": client.csrf})
    edit(git_folder, "timeline.yaml", FEED, 'action: "Neu"')
    assert client.post("/api/v1/sync", headers={"X-CSRF-Token": client.csrf}).status_code == 200

    conn = connect(tmp_path / "chronicle.db")
    runs = [row["detail"] for row in conn.execute("SELECT detail FROM audit_log WHERE action = 'sync'")]
    conn.close()
    assert runs == ["committed=False,merged=False,conflicts=0", "committed=True,merged=False,conflicts=0"]
