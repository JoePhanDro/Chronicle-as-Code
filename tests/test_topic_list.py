"""Topic list: status and latest event per topic, and the order that puts open topics first."""

import pytest

from tests.conftest import app_for
from chronicle import repository
from chronicle.db import connect


@pytest.fixture(autouse=True)
def insecure_cookies(monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")


def add_empty_topic(conn, key):
    conn.execute("INSERT INTO topics (key, title, kind) VALUES (?, 'Leer', 'project')", (key,))
    conn.commit()


def test_status_is_the_most_pressing_one_among_the_events(tmp_path, folder_with_b):
    conn = connect(tmp_path / "chronicle.db")
    topics = {topic["key"]: topic for topic in repository.list_topics(conn)}
    assert topics["inc-2026-042"]["status"] == "in_progress"  # in_progress and resolved events, none open
    assert topics["b"]["status"] == "open"
    assert topics["b"]["last_event_at"].startswith("2026-10-14T08:00")
    assert topics["b"]["event_count"] == 1


def test_topic_without_events_has_no_status(conn):
    add_empty_topic(conn, "leer")
    topic = next(item for item in repository.list_topics(conn) if item["key"] == "leer")
    assert (topic["status"], topic["last_event_at"], topic["event_count"]) == (None, None, 0)


def test_default_order_is_by_key_and_attention_order_puts_open_first(tmp_path, folder_with_b):
    conn = connect(tmp_path / "chronicle.db")
    add_empty_topic(conn, "a-leer")
    assert [topic["key"] for topic in repository.list_topics(conn)] == ["a-leer", "b", "inc-2026-042"]
    assert [topic["key"] for topic in repository.list_topics(conn, by_attention=True)] == ["b", "inc-2026-042", "a-leer"]


def test_topics_page_shows_status_in_text(api_client):
    client = api_client("viewer")
    body = client.get("/topics").text
    assert "in Arbeit" in body
    assert 'title="' in body  # the full title stays readable when it is cut off
    assert "data-list-row" in body  # the focus ring of a row stays inside the list
    assert "aria-label=\"Topics\"" in body


def test_empty_topic_list_explains_itself_and_offers_one_way_to_create(tmp_path):
    from fastapi.testclient import TestClient

    from chronicle import accounts, security
    from tests.conftest import PASSWORD

    path = tmp_path / "leer.db"
    conn = connect(path)
    accounts.create_user(conn, "anna", security.hash_password(PASSWORD), "analyst")
    accounts.create_user(conn, "vera", security.hash_password(PASSWORD), "viewer")
    conn.close()

    def page(username):
        client = TestClient(app_for(path))
        assert client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD}).status_code == 200
        return client.get("/topics").text

    writer = page("anna")
    assert "Noch keine Topics" in writer and "Erstes Topic anlegen" in writer
    assert writer.count('href="/topics/new"') == 2  # the "Neu" menu and the empty state, not a second header button
    viewer = page("vera")
    assert "Topics legen Analysten und Admins an." in viewer and "/topics/new" not in viewer
