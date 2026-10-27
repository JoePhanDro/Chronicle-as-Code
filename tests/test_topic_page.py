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
    # a topic that is the home of events takes them along: the button says how many
    assert "Entfernt das Topic samt seinen 7 Events" in panel and "Topic mit 7 Events löschen</button>" in panel


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


def test_topic_list_offers_a_filter_only_when_it_is_long(api_client):
    client = api_client("analyst")
    assert 'id="topic-filter"' not in client.get("/topics").text
    for number in range(6):
        create_empty_topic(client, key=f"t{number}")
    html = client.get("/topics").text
    assert 'id="topic-filter"' in html and 'id="topic-status"' in html and 'data-status=""' in html
    assert "event.key === 'Enter'" in html  # the filter never submits anything


def test_link_form_lives_in_the_edit_panel_with_an_entry_point_above_the_list(api_client):
    client = api_client("analyst")
    create_empty_topic(client)
    html = client.get("/topics/leer").text
    assert 'id="link-open"' in html and html.index('id="link-open"') < html.index('id="bearbeiten"')
    assert html.index('id="bearbeiten"') < html.index('id="verknuepfen"') < html.index('id="link-filter"')


def test_viewers_get_no_link_form_and_no_entry_point(api_client):
    html = api_client("viewer").get("/topics/inc-2026-042").text
    assert 'id="verknuepfen"' not in html and 'id="link-open"' not in html


def test_long_event_lists_show_five_rows_and_fold_the_rest(api_client):
    client = api_client("analyst")
    html = client.get("/topics/inc-2026-042").text  # seven events
    assert "Weitere 2 Events anzeigen" in html
    shown = html.split('aria-label="Events dieses Topics"')[1].split("</ul>")[0]
    assert shown.count('href="/events/') == 5
    assert html.count('href="/events/') >= 7  # the folded ones are still in the page
    create_empty_topic(client, key="kurz")
    client.post("/topics/kurz/link", data={"event": "feed-cve", "csrf_token": client.headers["X-CSRF-Token"]})
    assert "Weitere" not in client.get("/topics/kurz").text


def test_event_rows_carry_the_phase_colour_as_an_edge_and_name_the_phase(api_client):
    html = api_client("viewer").get("/topics/inc-2026-042").text
    rows = html.split('aria-label="Events dieses Topics"')[1].split("</ul>")[0]
    assert rows.count("absolute inset-y-2 left-1.5") == 5 and "background: #ef4444" in rows  # Identification/Exploit are red
    assert "· Exploit ·" in rows  # the word stays next to the colour


def test_topic_list_edge_follows_the_phase_of_the_latest_event(api_client):
    client = api_client("analyst")
    create_empty_topic(client)
    html = client.get("/topics").text
    row = html.split('href="/topics/inc-2026-042"')[1].split("</li>")[0]
    assert "left-1.5" in row and "background: #3b82f6" in row and "· Lessons Learned" in row  # latest event is the post-mortem
    assert "left-1.5" not in html.split('href="/topics/leer"')[1].split("</li>")[0]  # no events, no phase, no edge


def test_delete_dialog_names_the_events_the_evidence_and_the_way_back(api_client):
    html = api_client("analyst").get("/topics/inc-2026-042").text  # file mode
    question = js("Topic „INC-2026-042: Ausnutzung von CVE-2026-XXXX“ (inc-2026-042) und 7 Events (analysis, containment, exploit, feed-cve, lessons …) löschen?\n\n")
    assert question in html
    assert js("Die Datei des Topics wird gelöscht; den früheren Stand liefert Git, Evidence-Dateien bleiben im Ordner liegen.") in html


def test_a_topic_whose_events_trigger_events_elsewhere_has_no_delete_button_but_the_reason(api_client):
    client = api_client("analyst")
    create_empty_topic(client)
    body = {"key": "fremd-1", "t0": "2026-10-27T10:00:00+02:00", "phase": "Activity", "actor": "A", "action": "Folge",
            "status": "open", "triggered_by": ["lessons"], "topic": "leer", "topics": ["leer"]}
    assert client.post("/api/v1/events", json=body).status_code == 201
    html = client.get("/topics/inc-2026-042").text
    assert "Nicht löschbar: Events anderer Topics hängen an Events dieses Topics" in html
    assert 'href="/events/fremd-1"' in html and "löschen</button>" not in html.split('id="bearbeiten"')[1]


def test_close_button_names_the_count_and_what_it_does_not_touch(api_client):
    html = api_client("analyst").get("/topics/inc-2026-042").text  # three events are not resolved
    assert "Alle 3 offenen Events abschließen" in html and 'action="/topics/inc-2026-042/close-events"' in html
    assert js("Alle 3 offenen Events dieses Topics auf „abgeschlossen“ setzen?\n\nDanach gilt das Topic als abgeschlossen.") in html


def test_viewers_get_no_close_button_and_cannot_close(api_client):
    client = api_client("viewer")
    assert "close-events" not in client.get("/topics/inc-2026-042").text
    response = client.post("/topics/inc-2026-042/close-events", data={"csrf_token": client.headers["X-CSRF-Token"]})
    assert response.status_code == 403
    assert "in Arbeit" in client.get("/topics/inc-2026-042").text


def test_closing_resolves_every_home_event_and_the_topic_follows(api_client):
    client = api_client("analyst")
    response = client.post("/topics/inc-2026-042/close-events", data={"csrf_token": client.headers["X-CSRF-Token"]},
                           follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/topics/inc-2026-042?abgeschlossen=3"
    page = client.get(response.headers["location"]).text
    assert "3 Events abgeschlossen." in page and "abgeschlossen</span>" in page
    assert "close-events" not in page  # nothing left to close
    assert "in Arbeit" not in page.split('aria-label="Events dieses Topics"')[0]


def test_linked_events_of_other_topics_stay_as_they_are(api_client):
    client = api_client("analyst")
    create_empty_topic(client)
    token = client.headers["X-CSRF-Token"]
    assert "close-events" not in client.get("/topics/leer").text  # no events at all
    body = {"key": "eigen-1", "t0": "2026-10-27T10:00:00+02:00", "phase": "Activity", "actor": "A", "action": "Eigenes",
            "status": "open", "topic": "leer", "topics": ["leer"]}
    assert client.post("/api/v1/events", json=body).status_code == 201
    client.post("/topics/leer/link", data={"event": "exploit", "csrf_token": token})  # in progress, home is elsewhere
    html = client.get("/topics/leer").text
    assert "Offenes Event abschließen" in html  # only the home event counts
    assert js("1 verknüpftes Event aus anderen Topics bleibt unverändert, das Topic gilt dann noch nicht als abgeschlossen.") in html
    client.post("/topics/leer/close-events", data={"csrf_token": token})
    assert client.get("/api/v1/events/eigen-1").json()["status"] == "resolved"
    assert client.get("/api/v1/events/exploit").json()["status"] == "in_progress"
    assert "close-events" not in client.get("/topics/leer").text


def test_unknown_topic_gives_404(api_client):
    client = api_client("analyst")
    assert client.post("/topics/gibt-es-nicht/close-events", data={"csrf_token": client.headers["X-CSRF-Token"]}).status_code == 404
