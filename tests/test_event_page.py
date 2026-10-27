"""Event page, read view: the facts, what caused the event and what it caused, editing and deleting out of the way."""

import json
from pathlib import Path

import pytest

from chronicle import repository
from chronicle.pages import TEMPLATES

INPUT_CSS = (Path(__file__).resolve().parent.parent / "chronicle" / "static" / "input.css").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def insecure_cookies(monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")


def test_links_give_causes_and_effects_by_time(conn):
    links = repository.get_event_links(conn, "exploit")
    assert [event["key"] for event in links["causes"]] == ["feed-cve"]
    assert [event["key"] for event in links["effects"]] == ["analysis"]
    # an event with two effects lists both, earliest first
    assert [event["key"] for event in repository.get_event_links(conn, "feed-cve")["effects"]] == ["siem-alert", "exploit"]
    assert repository.get_event_links(conn, "gibt-es-nicht") == {"causes": [], "effects": []}


def test_phase_colour_has_a_neutral_fallback_for_old_data():
    colour = TEMPLATES.env.globals["phase_color"]
    assert colour("Exploit") == "#ef4444"
    assert colour("Phase aus alten Daten") == "#94a3b8"


def test_read_view_shows_phase_status_time_and_the_neighbouring_events(api_client):
    html = api_client("viewer").get("/events/exploit").text
    assert "Ausgelöst durch" in html and "Löste aus" in html
    assert 'href="/events/feed-cve"' in html and 'href="/events/analysis"' in html
    assert 'style="background: #ef4444"' in html  # the phase colour dot of Exploit
    assert "in Arbeit" in html  # status in words, not only in the edit form
    assert '<time data-utc="2026-10-14T06:40:00' in html  # honours the zone setting
    assert 'href="/events?actor=' in html and 'href="/events?system=wp-portal-01"' in html
    assert 'href="/topics/inc-2026-042"' in html


def test_viewers_see_neither_edit_panel_nor_delete(api_client):
    html = api_client("viewer").get("/events/exploit").text
    assert 'id="bearbeiten"' not in html and "Event löschen" not in html and 'id="edit-link"' not in html


def js(text):
    """How `tojson` writes text into the onsubmit handler: escaped, without the quotes."""
    return json.dumps(text)[1:-1]


def test_analysts_get_a_closed_edit_panel_with_delete_at_its_end(api_client):
    html = api_client("analyst").get("/events/lessons").text
    assert '<details id="bearbeiten" class="rounded-xl border border-white/10" >' in html  # closed
    assert html.index('id="bearbeiten"') < html.index("Event löschen</button>")  # delete is inside the panel, last
    assert 'id="edit-link"' in html


def test_delete_dialog_names_event_key_evidence_and_the_way_back(api_client):
    html = api_client("analyst").get("/events/lessons").text  # file mode, no evidence, nothing depends on it
    question = js("Event „Post-Mortem abgeschlossen“ (lessons) löschen?\n\n")
    assert question in html
    assert js("Es hat keine Evidence-Dateien. Die Datei des Topics ändert sich; den früheren Stand liefert Git.") in html


def test_delete_dialog_counts_the_evidence_files(api_client):
    client = api_client("analyst")
    token = client.headers["X-CSRF-Token"]
    upload = client.post("/events/lessons/evidence", data={"csrf_token": token, "type": "log"},
                         files={"file": ("a.log", b"zeile", "text/plain")}, follow_redirects=False)
    assert upload.status_code == 303
    assert js("Dabei wird 1 Evidence-Datei entfernt.") in client.get("/events/lessons").text


def test_an_event_that_others_depend_on_has_no_delete_button_but_the_reason(api_client):
    html = api_client("analyst").get("/events/exploit").text
    assert "Nicht löschbar: Dieses Event löst 1 weiteres Event aus" in html
    assert 'href="/events/analysis"' in html.split("Nicht löschbar")[1]
    assert "Event löschen</button>" not in html and 'action="/events/exploit/delete"' not in html


def test_without_a_data_folder_the_dialog_points_to_the_tombstones(tmp_path):
    from fastapi.testclient import TestClient

    from chronicle import accounts, security
    from chronicle.api import create_app
    from chronicle.db import connect
    from chronicle.importer import import_folder
    from tests.conftest import DATA_DIR, PASSWORD

    import_folder(tmp_path / "chronicle.db", DATA_DIR)
    conn = connect(tmp_path / "chronicle.db")
    accounts.create_user(conn, "anna", security.hash_password(PASSWORD), "analyst")
    conn.close()
    client = TestClient(create_app(tmp_path / "chronicle.db"))
    client.post("/api/v1/auth/login", json={"username": "anna", "password": PASSWORD})
    html = client.get("/events/lessons").text
    assert js("Ein Admin kann es unter Verwaltung, Gelöschte Events wiederherstellen.") in html


def mark(client, key, **fields):
    page = client.get(f"/events/{key}").text
    version = page.split('name="version" value="')[1].split('"')[0]
    token = client.headers["X-CSRF-Token"]
    return client.post(f"/events/{key}/mark", data={"csrf_token": token, "version": version, **fields}, follow_redirects=False)


def test_analysts_change_status_and_phase_on_the_event_page(api_client):
    client = api_client("analyst")
    html = client.get("/events/lessons").text
    assert 'action="/events/lessons/mark"' in html and '<select name="status"' in html and '<select name="phase"' in html
    response = mark(client, "lessons", status="open", phase="Lessons Learned")
    assert response.status_code == 303 and response.headers["location"] == "/events/lessons?gespeichert=status"
    page = client.get(response.headers["location"]).text
    assert 'value="open" selected' in page and "Status gespeichert" in page
    assert mark(client, "lessons", status="open", phase="Analysis").headers["location"].endswith("gespeichert=phase")
    assert 'value="Analysis" selected' in client.get("/events/lessons").text


def test_viewers_see_status_and_phase_as_text_and_cannot_change_them(api_client):
    client = api_client("viewer")
    html = client.get("/events/lessons").text
    assert "/mark" not in html and '<select name="status"' not in html
    assert mark_attempt(client).status_code == 403


def mark_attempt(client):
    return client.post("/events/lessons/mark", data={"csrf_token": client.headers["X-CSRF-Token"], "version": "1", "status": "open"})


def test_a_stale_version_is_reported_and_changes_nothing(api_client):
    client = api_client("analyst")
    token = client.headers["X-CSRF-Token"]
    response = client.post("/events/lessons/mark", data={"csrf_token": token, "version": "999", "status": "open"})
    assert response.status_code == 409 and 'role="alert"' in response.text


def test_edit_panel_no_longer_asks_for_status_and_phase_but_keeps_them(api_client):
    html = api_client("analyst").get("/events/lessons").text.split('id="bearbeiten"')[1]
    assert '<select name="status"' not in html and '<select name="phase"' not in html
    assert 'type="hidden" name="status"' in html and 'type="hidden" name="phase"' in html


def test_a_failed_edit_opens_the_panel_again(api_client):
    client = api_client("analyst")
    token = client.get("/api/v1/auth/me").json()["csrf_token"]
    response = client.post("/events/exploit/edit", data={"csrf_token": token, "version": "999"})
    assert response.status_code in (409, 422, 200)
    assert '<details id="bearbeiten" class="rounded-xl border border-white/10" open>' in response.text
    assert 'role="alert"' in response.text


def test_markdown_headings_in_details_stay_below_the_page_title():
    assert ":root:root .prose-chronicle h1 { font-size: 1.25em; }" in INPUT_CSS  # 17.5px under a 20px title


def test_evidence_upload_is_folded_away_and_opens_again_after_a_failed_upload(api_client):
    client = api_client("analyst")
    html = client.get("/events/lessons").text
    assert '<details id="evidence-add" class="mt-4 rounded-xl border border-white/10" >' in html and "Evidence hinzufügen" in html
    token = client.headers["X-CSRF-Token"]
    failed = client.post("/events/lessons/evidence", data={"csrf_token": token, "type": "log"},
                         files={"file": ("a.exe", b"x", "application/octet-stream")})
    assert failed.status_code == 422
    assert '<details id="evidence-add" class="mt-4 rounded-xl border border-white/10" open>' in failed.text
    assert '<details id="bearbeiten" class="rounded-xl border border-white/10" >' in failed.text  # the edit panel stays closed


def test_chips_say_that_they_can_be_changed_and_the_panel_names_what_it_holds(api_client):
    html = api_client("analyst").get("/events/lessons").text
    assert 'title="Phase ändern"' in html and 'title="Status ändern"' in html
    assert "Weitere Felder bearbeiten · Event löschen</summary>" in html
