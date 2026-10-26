"""Neu laden: the topic files are read again after changes made outside the app (ADR 0008)."""

import pytest
from fastapi.testclient import TestClient

from chronicle import accounts, repository, security
from chronicle.api import create_app
from chronicle.db import connect
from chronicle.pages import _local_path, _with_param
from tests.conftest import PASSWORD


@pytest.fixture(autouse=True)
def insecure_cookies(monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")


def post_reload(client, next_path="/events"):
    return client.post("/neu-laden", data={"csrf_token": client.headers["X-CSRF-Token"], "next": next_path},
                       follow_redirects=False)


def test_button_is_shown_to_writers_in_file_mode_only(api_client):
    html = api_client("analyst").get("/topics").text
    assert 'action="/neu-laden"' in html and ">Neu laden</button>" in html
    assert 'name="next" value="/topics"' in html


def test_viewers_do_not_see_the_button_and_cannot_reload(api_client):
    client = api_client("viewer")
    assert "/neu-laden" not in client.get("/topics").text
    assert post_reload(client).status_code == 403


def test_button_is_absent_without_a_data_folder(tmp_path):
    conn = connect(tmp_path / "chronicle.db")
    accounts.create_user(conn, "anna", security.hash_password(PASSWORD), "analyst")
    conn.close()
    client = TestClient(create_app(tmp_path / "chronicle.db"))
    token = client.post("/api/v1/auth/login", json={"username": "anna", "password": PASSWORD}).json()["csrf_token"]
    assert "/neu-laden" not in client.get("/topics").text
    response = client.post("/neu-laden", data={"csrf_token": token}, follow_redirects=False)
    assert response.status_code == 409 and "kein Datenordner" in response.text


def test_requires_the_csrf_token(api_client):
    client = api_client("analyst")
    assert client.post("/neu-laden", data={"next": "/events"}, follow_redirects=False).status_code == 403


def test_a_change_in_the_file_shows_up_after_reload(api_client, folder):
    client = api_client("analyst")
    assert "Host isoliert" in client.get("/events").text
    path = folder / "timeline.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("Host isoliert", "Host vom Netz getrennt"), encoding="utf-8")
    assert "Host vom Netz getrennt" not in client.get("/events").text  # the index does not look at the files by itself
    response = post_reload(client, "/events?q=host")
    assert response.status_code == 303
    assert response.headers["location"] == "/events?q=host&neu_geladen=1"
    page = client.get(response.headers["location"]).text
    assert "Host vom Netz getrennt" in page and "Die Topic-Dateien sind neu eingelesen." in page


def test_a_broken_file_changes_nothing_and_lists_the_problems(api_client, folder):
    client = api_client("analyst")
    (folder / "timeline.yaml").write_text("topic: [kaputt\n", encoding="utf-8")
    response = post_reload(client, "/topics")
    assert response.status_code == 422
    assert "Neu laden nicht möglich" in response.text and "Es wurde nichts übernommen" in response.text
    assert 'role="alert"' in response.text and 'href="/topics"' in response.text
    assert "Host isoliert" in client.get("/events").text  # the index kept its earlier state


def test_a_successful_reload_is_written_to_the_audit_log(api_client, folder):
    client = api_client("analyst")
    post_reload(client)
    conn = connect(folder.parent / "chronicle.db")
    assert [row["action"] for row in conn.execute("SELECT action FROM audit_log WHERE action = 'reindex'")] == ["reindex"]
    conn.close()
    assert repository.list_topics(connect(folder.parent / "chronicle.db"))


@pytest.mark.parametrize("value", ["https://evil.example/", "//evil.example", "/\\evil", "evil", None, "/a\nb"])
def test_next_must_be_a_local_path(value):
    assert _local_path(value) == "/"


def test_next_keeps_local_paths_and_their_query():
    assert _local_path("/topics/inc-2026-042?x=1") == "/topics/inc-2026-042?x=1"
    assert _with_param("/events?q=a&neu_geladen=1", "neu_geladen", "1") == "/events?q=a&neu_geladen=1"
    assert _with_param("/topics", "neu_geladen", "1") == "/topics?neu_geladen=1"
