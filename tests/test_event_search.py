"""Search, filters and paging of the event list (issue #30)."""

import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from chronicle import accounts, repository, security
from chronicle.api import create_app
from chronicle.db import connect
from chronicle.repository import EventFilter

PASSWORD = "correct horse battery"
BULK_COUNT = 10_000
BULK_START = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.fixture(autouse=True)
def insecure_cookies(monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")


def login(db_path, username):
    client = TestClient(create_app(db_path))
    response = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client


def create_users(path):
    conn = connect(path)
    accounts.create_user(conn, "anna", security.hash_password(PASSWORD), "analyst")
    accounts.create_user(conn, "viewer", security.hash_password(PASSWORD), "viewer")
    conn.close()


def keys_of(events):
    return sorted(event["key"] for event in events)


@pytest.fixture
def search_db(tmp_path):
    path = tmp_path / "search.db"
    create_users(path)
    anna = login(path, "anna")
    anna.post("/api/v1/topics", json={"key": "suche", "title": "Suche", "kind": "incident"})
    events = [
        {"key": "e1", "t0": "2026-10-10T08:00:00+00:00", "phase": "Identification", "actor": "SIEM",
         "action": "Alarm", "status": "open", "details": "Port-Scan aus dem Netz"},
        {"key": "e2", "t0": "2026-10-11T09:00:00+02:00", "phase": "Containment", "actor": "Anna Müller",
         "action": "Sperre gesetzt", "status": "in_progress", "details": "Firewall-Regel angepasst"},
        {"key": "e3", "t0": "2026-10-12T12:00:00+00:00", "phase": "Containment", "actor": "SIEM",
         "action": "Webshell entfernt", "status": "resolved", "details": "Datei shell.php gelöscht"},
        {"key": "e4", "t0": "2026-10-13T06:30:00+00:00", "phase": "Lessons Learned", "actor": "Projekt",
         "action": "Auswertung", "status": "resolved", "details": ""},
    ]
    for event in events:
        response = anna.post("/api/v1/events", json={**event, "topic": "suche"})
        assert response.status_code == 201, response.text
    anna.close()
    return path


def search(path, **params):
    response = login(path, "viewer").get("/api/v1/events", params=params)
    assert response.status_code == 200, response.text
    return keys_of(response.json())


def test_search_finds_words_in_action_details_and_actor(search_db):
    assert search(search_db, q="webshell") == ["e3"]
    assert search(search_db, q="shell.php") == ["e3"]  # punctuation separates words
    assert search(search_db, q="siem") == ["e1", "e3"]  # actor
    assert search(search_db, q="port scan") == ["e1"]  # details, every word has to match
    assert search(search_db, q="firewall webshell") == []


def test_search_matches_prefixes_and_ignores_accents(search_db):
    assert search(search_db, q="mull") == ["e2"]
    assert search(search_db, q="muller") == ["e2"]  # ü matches u
    assert search(search_db, q="sperr") == ["e2"]


def test_search_input_cannot_break_the_query(search_db):
    for text in ['"', "NOT", "a OR", "(", "*", "a:b", "AND AND"]:
        response = login(search_db, "viewer").get("/api/v1/events", params={"q": text})
        assert response.status_code == 200, text


def test_filters_by_phase_status_actor_and_day(search_db):
    assert search(search_db, phase="Containment") == ["e2", "e3"]
    assert search(search_db, status="resolved") == ["e3", "e4"]
    assert search(search_db, actor="siem") == ["e1", "e3"]  # not case-sensitive
    assert search(search_db, since="2026-10-11", until="2026-10-12") == ["e2", "e3"]  # UTC days, both inclusive
    assert search(search_db, phase="Containment", actor="siem") == ["e3"]


def test_filters_combine_with_the_topic(search_db):
    anna = login(search_db, "anna")
    anna.post("/api/v1/topics", json={"key": "andere", "title": "Andere", "kind": "other"})
    anna.post("/api/v1/topics/andere/events", json={"event": "e3"})
    assert search(search_db, topic="andere", q="webshell") == ["e3"]
    assert search(search_db, topic="andere", q="siem", status="open") == []


def test_invalid_filter_values_are_rejected_by_the_api(search_db):
    viewer = login(search_db, "viewer")
    assert viewer.get("/api/v1/events", params={"phase": "Unbekannt"}).status_code == 422
    assert viewer.get("/api/v1/events", params={"status": "irgendwann"}).status_code == 422
    assert viewer.get("/api/v1/events", params={"since": "2026-13-01"}).status_code == 422


def test_event_list_page_applies_the_filters(search_db):
    viewer = login(search_db, "viewer")
    html = viewer.get("/events", params={"q": "siem", "status": "resolved"}).text
    assert "Webshell entfernt" in html
    assert "Alarm" not in html
    assert "1 Event gefunden" in html
    assert "Filter zurücksetzen" in html


def test_event_list_page_says_when_nothing_matches(search_db):
    html = login(search_db, "viewer").get("/events", params={"q": "gibt-es-nicht"}).text
    assert "Keine Events passen zu den Filtern." in html


def test_invalid_page_filter_values_are_ignored(search_db):
    viewer = login(search_db, "viewer")
    html = viewer.get("/events", params={"phase": "Unbekannt", "since": "gestern"}).text
    for action in ("Alarm", "Sperre gesetzt", "Webshell entfernt", "Auswertung"):
        assert action in html


def test_index_follows_updates_and_deletes(search_db):
    conn = connect(search_db)
    with conn:
        conn.execute("UPDATE events SET action = 'Netzwerk getrennt' WHERE key = 'e1'")
        conn.execute("DELETE FROM events WHERE key = 'e4'")
    found = {
        "old": [event["key"] for event in repository.find_events(conn, EventFilter(query="alarm"))],
        "new": [event["key"] for event in repository.find_events(conn, EventFilter(query="getrennt"))],
        "deleted": [event["key"] for event in repository.find_events(conn, EventFilter(query="auswertung"))],
    }
    conn.close()
    assert found == {"old": [], "new": ["e1"], "deleted": []}


def test_existing_database_gets_the_index_when_opened(tmp_path):
    path = tmp_path / "old.db"
    conn = connect(path)
    with conn:
        conn.execute(
            "INSERT INTO events (key, t0, phase, actor, action, status, home_topic) "
            "VALUES ('alt', '2026-10-01T10:00:00+00:00', 'Activity', 'Altsystem', 'Vor dem Index', 'open', 'x')"
        )
        # Simulate a database from before issue #30: no index, no triggers.
        for trigger in ("events_fts_insert", "events_fts_delete", "events_fts_update"):
            conn.execute(f"DROP TRIGGER {trigger}")
        conn.execute("DROP TABLE events_fts")
    conn.close()

    conn = connect(path)
    found = [event["key"] for event in repository.find_events(conn, EventFilter(query="altsystem"))]
    conn.close()
    assert found == ["alt"]


def write_bulk_events(path, count):
    conn = connect(path)
    with conn:
        topic_pk = conn.execute("INSERT INTO topics (key, title, kind) VALUES ('bulk', 'Lasttest', 'incident')").lastrowid
        for index in range(count):
            # Every thousandth event is a webshell; the others only differ by their number.
            action = "Webshell entdeckt" if index % 1000 == 0 else f"Alarm {index}"
            t0 = (BULK_START + timedelta(minutes=index)).isoformat()
            event_pk = conn.execute(
                "INSERT INTO events (key, t0, phase, actor, action, status, home_topic) "
                "VALUES (?, ?, 'Identification', 'SIEM', ?, 'open', 'bulk')",
                (f"bulk-{index:05d}", t0, action),
            ).lastrowid
            conn.execute("INSERT INTO topic_events (topic_pk, event_pk) VALUES (?, ?)", (topic_pk, event_pk))
    conn.close()


@pytest.fixture
def bulk_db(tmp_path):
    path = tmp_path / "bulk.db"
    create_users(path)
    write_bulk_events(path, BULK_COUNT)
    return path


def test_list_of_ten_thousand_events_loads_quickly(bulk_db):
    viewer = login(bulk_db, "viewer")
    started = time.perf_counter()
    response = viewer.get("/events")
    elapsed = time.perf_counter() - started

    assert response.status_code == 200
    assert elapsed < 5, f"event list took {elapsed:.1f} s"
    assert response.text.count('href="/events/bulk-') == 50
    assert f"{BULK_COUNT} Events" in response.text
    assert f"Seite 1 von {BULK_COUNT // 50}" in response.text


def test_pages_follow_each_other_without_gaps(bulk_db):
    viewer = login(bulk_db, "viewer")
    first = viewer.get("/events", params={"page": 1}).text
    second = viewer.get("/events", params={"page": 2}).text
    assert "bulk-00049" in first and "bulk-00050" not in first
    assert "bulk-00050" in second and "bulk-00099" in second and "bulk-00100" not in second
    assert "Seite 2 von 200" in second
    assert 'href="/events?page=3"' in second


def test_page_past_the_end_shows_the_last_page(bulk_db):
    html = login(bulk_db, "viewer").get("/events", params={"page": 99999}).text
    assert "Seite 200 von 200" in html
    assert "bulk-09999" in html


def test_invalid_page_number_shows_the_first_page(bulk_db):
    html = login(bulk_db, "viewer").get("/events", params={"page": "zwei"}).text
    assert "Seite 1 von 200" in html


def test_page_links_keep_the_filters(bulk_db):
    html = login(bulk_db, "viewer").get("/events", params={"q": "webshell"}).text
    assert "10 Events gefunden" in html
    assert "Seite" not in html  # ten results fit on one page

    html = login(bulk_db, "viewer").get("/events", params={"q": "alarm"}).text
    assert 'href="/events?q=alarm&amp;page=2"' in html


def test_search_on_ten_thousand_events_finds_the_webshells(bulk_db):
    viewer = login(bulk_db, "viewer")
    response = viewer.get("/api/v1/events", params={"q": "webshell"})
    assert len(response.json()) == 10
    assert keys_of(response.json())[0] == "bulk-00000"


def test_day_filter_on_ten_thousand_events(bulk_db):
    # The events run from 1 Oct 00:00 UTC in one-minute steps; 7 Oct starts at minute 8640.
    viewer = login(bulk_db, "viewer")
    response = viewer.get("/api/v1/events", params={"since": "2026-10-07", "until": "2026-10-07"})
    assert len(response.json()) == BULK_COUNT - 8640
