"""The Abgleichen page (issue #90): the preview, the button, and what the page shows after a run."""

import shutil
import subprocess
from pathlib import Path

import pytest

from chronicle import accounts, security
from chronicle.api import create_app
from chronicle.db import connect

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PASSWORD = "correct horse battery"
ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git ist nicht installiert")


def git(folder: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(folder), *args], capture_output=True, text=True, check=True).stdout


@pytest.fixture(autouse=True)
def plain_http(monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")
    for name, value in {"GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                        "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@example.invalid"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("PYTHONPATH", str(ROOT))


@pytest.fixture
def folder(tmp_path):
    data = tmp_path / "data"
    shutil.copytree(DATA_DIR, data)
    git(data, "init", "-q", "-b", "main")
    git(data, "add", ".")
    git(data, "commit", "-q", "-m", "Stand")
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(data, "remote", "add", "origin", str(remote))
    git(data, "push", "-q", "-u", "origin", "main")
    return data


def edit(folder: Path, old: str, new: str) -> None:
    path = folder / "timeline.yaml"
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, old
    path.write_text(text.replace(old, new), encoding="utf-8")


def client_for(tmp_path, folder, role="analyst", data_dir=True):
    from fastapi.testclient import TestClient

    conn = connect(tmp_path / "chronicle.db")
    accounts.create_user(conn, "anna", security.hash_password(PASSWORD), role)
    conn.close()
    client = TestClient(create_app(tmp_path / "chronicle.db", data_dir=folder if data_dir else None))
    response = client.post("/api/v1/auth/login", json={"username": "anna", "password": PASSWORD})
    assert response.status_code == 200, response.text
    client.csrf = response.json()["csrf_token"]
    return client


def test_the_page_shows_the_preview_and_the_button(tmp_path, folder):
    edit(folder, 'action: "CVE-2026-XXXX veröffentlicht"', 'action: "Neu formuliert"')
    client = client_for(tmp_path, folder)

    response = client.get("/abgleich")

    assert response.status_code == 200
    assert "geändert" in response.text and "feed-cve" in response.text
    assert 'action="/abgleich"' in response.text


def test_a_viewer_is_refused(tmp_path, folder):
    client = client_for(tmp_path, folder, role="viewer")

    assert client.get("/abgleich").status_code == 403


def test_running_the_sync_pushes_and_says_so(tmp_path, folder):
    edit(folder, 'action: "CVE-2026-XXXX veröffentlicht"', 'action: "Neu"')
    client = client_for(tmp_path, folder)

    response = client.post("/abgleich", data={"csrf_token": client.csrf})

    assert response.status_code == 200
    assert "Abgeglichen." in response.text
    assert "Lokale Änderungen sind committet." in response.text
    assert git(folder, "log", "-1", "--format=%s").startswith("Abgleich ")


def test_the_run_is_refused_without_the_csrf_token(tmp_path, folder):
    client = client_for(tmp_path, folder)

    assert client.post("/abgleich", data={"csrf_token": "falsch"}).status_code == 403


def test_a_conflict_is_shown_and_nothing_is_pushed(tmp_path, folder):
    remote = tmp_path / "remote.git"
    other = tmp_path / "anna"
    subprocess.run(["git", "clone", "-q", str(remote), str(other)], capture_output=True, text=True, check=True)
    (other / "timeline.yaml").write_text(
        (other / "timeline.yaml").read_text(encoding="utf-8").replace(
            'action: "CVE-2026-XXXX veröffentlicht"', 'action: "Von anderswo"'), encoding="utf-8")
    git(other, "commit", "-q", "-am", "Anders")
    git(other, "push", "-q", "origin", "main")
    edit(folder, 'action: "CVE-2026-XXXX veröffentlicht"', 'action: "Hier"')
    client = client_for(tmp_path, folder)

    response = client.post("/abgleich", data={"csrf_token": client.csrf})

    assert response.status_code == 200
    assert "Konflikt in timeline.yaml" in response.text
    assert git(remote, "log", "-1", "--format=%s", "main").startswith("Anders")


def test_without_a_data_folder_the_page_says_so(tmp_path, folder):
    client = client_for(tmp_path, folder, data_dir=False)

    response = client.get("/abgleich")

    assert response.status_code == 200
    assert "Kein Datenordner konfiguriert." in response.text


def test_the_menu_offers_the_page_to_writers_only(tmp_path, folder):
    writer = client_for(tmp_path, folder)
    assert 'href="/abgleich"' in writer.get("/events").text

    viewer_dir = tmp_path / "viewer"
    viewer_dir.mkdir()
    viewer = client_for(viewer_dir, folder, role="viewer")
    assert 'href="/abgleich"' not in viewer.get("/events").text


def test_a_run_is_recorded_in_the_audit_log(tmp_path, folder):
    edit(folder, 'action: "CVE-2026-XXXX veröffentlicht"', 'action: "Neu"')
    client = client_for(tmp_path, folder)
    client.post("/abgleich", data={"csrf_token": client.csrf})

    conn = connect(tmp_path / "chronicle.db")
    actions = [row["action"] for row in conn.execute("SELECT action FROM audit_log")]
    conn.close()
    assert "sync" in actions
