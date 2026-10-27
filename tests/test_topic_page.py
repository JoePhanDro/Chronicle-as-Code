"""Topic page: status line, a dashboard frame that sizes itself, delete and edit out of the way, a filter for linking."""

import json

import pytest

from chronicle import repository


@pytest.fixture(autouse=True)
def insecure_cookies(monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")


def js(text):
    """How `tojson` writes text into the onsubmit handler: escaped, without the quotes."""
    return json.dumps(text)[1:-1]


def create_empty_topic(client, key="leer"):
    response = client.post("/api/v1/topics", json={"key": key, "title": "Leeres Topic", "kind": "project"})
    assert response.status_code == 201, response.text


def test_most_pressing_status():
    assert repository.most_pressing_status(["resolved", "open", "in_progress"]) == "open"
    assert repository.most_pressing_status(["resolved", "in_progress"]) == "in_progress"
    assert repository.most_pressing_status(["resolved"]) == "resolved"
    assert repository.most_pressing_status([]) is None


def test_status_line_gives_status_count_and_latest_event(api_client):
    html = api_client("viewer").get("/topics/inc-2026-042").text
    assert "in Arbeit" in html and "7 Events" in html
    assert '<time class="font-mono text-xs" data-utc="2026-10-16T08:00' in html  # honours the zone setting


def test_frame_asks_for_the_embedded_dashboard_without_a_second_title(api_client):
    client = api_client("viewer")
    assert 'src="/topics/inc-2026-042/dashboard?embed=1"' in client.get("/topics/inc-2026-042").text
    embedded = client.get("/topics/inc-2026-042/dashboard?embed=1").text
    standalone = client.get("/topics/inc-2026-042/dashboard").text
    assert "<h1" not in embedded and "window.frameElement" in embedded
    assert "<h1" in standalone and "window.frameElement" not in standalone  # the new-window link keeps its header


def test_viewer_sees_no_edit_panel_and_no_delete(api_client):
    html = api_client("viewer").get("/topics/inc-2026-042").text
    assert 'id="bearbeiten"' not in html and "Topic löschen" not in html and 'id="edit-link"' not in html


def test_analyst_edit_panel_is_closed_and_delete_sits_inside_it(api_client):
    html = api_client("analyst").get("/topics/inc-2026-042").text
    panel = html[html.index('<details id="bearbeiten"'):]
    assert panel.startswith('<details id="bearbeiten" class="rounded-xl border border-white/10" >')  # closed
    assert 'id="edit-link"' in html
    # a topic that is the home of events cannot be deleted: the button is replaced by the reason
    assert "Heimat von 7 Events" in panel and "Topic löschen</button>" not in panel


def test_delete_of_an_empty_topic_names_title_and_key_in_the_confirmation(api_client):
    client = api_client("analyst")
    create_empty_topic(client)
    html = client.get("/topics/leer").text
    assert "Topic löschen</button>" in html
    assert js("Topic löschen?") in html and "Leeres Topic (leer)" in html


def test_empty_topic_has_no_frame_and_offers_the_first_event(api_client):
    client = api_client("analyst")
    create_empty_topic(client)
    html = client.get("/topics/leer").text
    assert "<iframe" not in html
    assert "Noch keine Events in diesem Topic" in html and 'href="/events/new?topic=leer"' in html


def test_new_event_page_preselects_the_topic_from_the_link(api_client):
    client = api_client("analyst")
    create_empty_topic(client)
    html = client.get("/events/new?topic=leer").text
    assert '<option value="leer" selected>' in html
    assert "selected>" not in client.get("/events/new?topic=gibt-es-nicht").text.split('name="topic"')[1].split("</select>")[0]


def test_unlink_asks_with_the_event_named(api_client):
    client = api_client("analyst")
    create_empty_topic(client)
    token = client.headers["X-CSRF-Token"]
    client.post("/topics/leer/link", data={"event": "feed-cve", "csrf_token": token})
    html = client.get("/topics/leer").text
    assert js("Verknüpfung lösen?") in html and "(feed-cve)" in html and 'id="link-filter"' in html


def test_events_come_before_the_frame_and_the_frame_can_be_skipped(api_client):
    html = api_client("viewer").get("/topics/inc-2026-042").text
    assert html.index('aria-label="Events dieses Topics"') < html.index("<iframe")
    assert html.index('href="#nach-zeitleiste"') < html.index("<iframe") < html.index('id="nach-zeitleiste"')


def test_only_linked_events_are_marked_not_the_home_ones(api_client):
    client = api_client("analyst")
    create_empty_topic(client)
    token = client.headers["X-CSRF-Token"]
    client.post("/topics/leer/link", data={"event": "feed-cve", "csrf_token": token})
    assert "Heimat</span>" not in client.get("/topics/inc-2026-042").text
    assert "verknüpft</span>" in client.get("/topics/leer").text


def test_link_filter_does_not_submit_on_enter_and_has_its_own_heading(api_client):
    client = api_client("analyst")
    create_empty_topic(client)
    html = client.get("/topics/leer").text
    assert "event.key === 'Enter'" in html and 'id="link-heading"' in html


def test_embedded_dashboard_has_no_phase_legend_and_no_empty_detail_panel(api_client):
    client = api_client("viewer")
    embedded = client.get("/topics/inc-2026-042/dashboard?embed=1").text
    standalone = client.get("/topics/inc-2026-042/dashboard").text
    assert "#details:empty { display: none; }" in embedded and "#details:empty" not in standalone
    assert 'rounded-full bg-white/[.03] px-2.5 py-1' not in embedded
    assert 'rounded-full bg-white/[.03] px-2.5 py-1' in standalone
