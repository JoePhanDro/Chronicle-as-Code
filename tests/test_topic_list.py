"""Topic list: status and latest event per topic, and the order that puts open topics first."""

import pytest

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
    assert "aria-label=\"Topics\"" in body
