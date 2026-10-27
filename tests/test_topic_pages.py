"""The topic pages: who may change what, and what the forms answer to a refused change."""

import pytest
from fastapi.testclient import TestClient

from chronicle import repository
from chronicle.api import create_app

TOPIC = "inc-2026-042"
WRITES = [
    ("/topics/new", {"title": "X"}),
    (f"/topics/{TOPIC}/edit", {"title": "X"}),
    (f"/topics/{TOPIC}/delete", {}),
    (f"/topics/{TOPIC}/close-events", {}),
    (f"/topics/{TOPIC}/link", {"event": "feed-cve"}),
    (f"/topics/{TOPIC}/unlink/exploit", {}),
]
READS = ["/topics", "/topics/new", f"/topics/{TOPIC}", f"/topics/{TOPIC}/dashboard"]


@pytest.fixture(autouse=True)
def insecure_cookies(monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")


@pytest.fixture
def anna(api_client):
    return api_client("analyst")


def csrf_of(client):
    return {"csrf_token": client.headers["X-CSRF-Token"]}


def post(client, path, **data):
    return client.post(path, data={**csrf_of(client), **data}, follow_redirects=False)


def new_topic(client, title="Projekt X"):
    created = post(client, "/topics/new", title=title, kind="project")
    assert created.status_code == 303
    return created.headers["location"].rsplit("/", 1)[-1]


def test_nothing_is_open_without_a_login(folder):
    visitor = TestClient(create_app(folder.parent / "chronicle.db", data_dir=folder))
    for path in READS:
        assert visitor.get(path, follow_redirects=False).headers["location"] == "/login", path
    for path, data in WRITES:
        response = visitor.post(path, data=data, follow_redirects=False)
        assert (response.status_code, response.headers["location"]) == (303, "/login"), path


def test_a_viewer_reads_but_changes_nothing(api_client):
    viewer = api_client("viewer")
    for path, data in WRITES:
        assert post(viewer, path, **data).status_code == 403, path
    assert viewer.get(f"/topics/{TOPIC}").status_code == 200


def test_a_form_without_the_csrf_token_is_refused(anna):
    for path, data in WRITES:
        assert anna.post(path, data=data, follow_redirects=False).status_code == 403, path


def test_the_new_topic_form_answers_a_missing_title_with_422(anna):
    refused = post(anna, "/topics/new", title="  ", kind="project")
    assert refused.status_code == 422


def test_a_topic_is_renamed_and_the_page_shows_it(anna):
    key = new_topic(anna)
    assert post(anna, f"/topics/{key}/edit", title="Neuer Name", kind="project").status_code == 303
    assert "Neuer Name" in anna.get(f"/topics/{key}").text


def test_editing_with_an_empty_title_shows_the_error_on_the_topic_page(anna):
    key = new_topic(anna)
    refused = post(anna, f"/topics/{key}/edit", title="", kind="project")
    assert refused.status_code == 422 and key in refused.text


def test_editing_an_unknown_topic_is_404(anna):
    assert post(anna, "/topics/gibt-es-nicht/edit", title="X", kind="project").status_code == 404


def test_an_empty_topic_is_deleted_and_the_list_follows(anna):
    key = new_topic(anna)
    deleted = post(anna, f"/topics/{key}/delete")
    assert (deleted.status_code, deleted.headers["location"]) == (303, "/topics")
    assert anna.get(f"/topics/{key}").status_code == 404


def test_deleting_an_unknown_topic_is_404(anna):
    assert post(anna, "/topics/gibt-es-nicht/delete").status_code == 404


def test_a_topic_whose_events_others_depend_on_is_not_deleted(anna, conn):
    key = new_topic(anna)
    # an event of another topic that is triggered by an event at home in the new topic
    data = {"key": "ursache", "t0": "2026-10-20T10:00:00+02:00", "phase": "Analysis", "actor": "A",
            "action": "Ursache", "status": "open", "topic": key}
    assert anna.post("/api/v1/events", json=data).status_code == 201
    follower = {**data, "key": "folge", "action": "Folge", "topic": TOPIC, "triggered_by": ["ursache"]}
    assert anna.post("/api/v1/events", json=follower).status_code == 201

    refused = post(anna, f"/topics/{key}/delete")
    assert refused.status_code == 409 and "folge" in refused.text
    assert repository.get_topic(conn, key) is not None


def test_closing_the_events_resolves_those_at_home_and_reports_the_number(anna, conn):
    open_at_home = [event for event in repository.list_topic_events(conn, TOPIC)
                    if event["home_topic"] == TOPIC and event["status"] != "resolved"]
    assert open_at_home

    done = post(anna, f"/topics/{TOPIC}/close-events")
    assert done.status_code == 303
    assert done.headers["location"] == f"/topics/{TOPIC}?abgeschlossen={len(open_at_home)}"
    assert all(event["status"] == "resolved" for event in repository.list_topic_events(conn, TOPIC)
               if event["home_topic"] == TOPIC)
    assert anna.get(done.headers["location"]).status_code == 200
    assert anna.get(f"/topics/{TOPIC}?abgeschlossen=x").status_code == 200  # not a number: ignored


def test_closing_the_events_of_an_unknown_topic_is_404(anna):
    assert post(anna, "/topics/gibt-es-nicht/close-events").status_code == 404


def test_an_event_is_linked_into_a_topic_and_unlinked_again(anna):
    key = new_topic(anna)
    assert post(anna, f"/topics/{key}/link", event="feed-cve").status_code == 303
    assert "feed-cve" in anna.get(f"/topics/{key}").text
    assert post(anna, f"/topics/{key}/unlink/feed-cve").status_code == 303
    assert "feed-cve" not in [event["key"] for event in anna.get(f"/api/v1/topics/{key}/events").json()]


def test_linking_an_unknown_event_is_404_and_unlinking_the_home_event_is_422(anna):
    assert post(anna, f"/topics/{TOPIC}/link", event="gibt-es-nicht").status_code == 404
    assert post(anna, f"/topics/{TOPIC}/unlink/exploit").status_code == 422


def test_the_dashboard_of_a_topic_is_rendered_and_an_unknown_one_is_404(anna):
    assert anna.get(f"/topics/{TOPIC}/dashboard").status_code == 200
    assert anna.get(f"/topics/{TOPIC}/dashboard?embed=1").status_code == 200
    assert anna.get("/topics/gibt-es-nicht/dashboard").status_code == 404
