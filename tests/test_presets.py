"""Saved search presets as YAML files in presets/ of the data folder (issue #260):

- save/load round-trip, strict validation, bounded scan gates
- the loader ignores the presets folder, so a preset never becomes a topic
- the event list applies a preset (?preset=name), an explicit parameter overrides it
- saving via POST /events/presets needs a writer role and the CSRF token, writes atomically
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from chronicle import accounts, presets, security
from chronicle.db import connect
from chronicle.loader import find_topic_files
from chronicle.models import Phase
from chronicle.queries import EventFilter
from chronicle.store import StoreError
from tests.conftest import app_for, example_folder, import_example

PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def insecure_cookies(monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "presets.db"
    conn = connect(path)
    accounts.create_user(conn, "admin", security.hash_password(PASSWORD), "admin")
    accounts.create_user(conn, "anna", security.hash_password(PASSWORD), "analyst")
    accounts.create_user(conn, "viewer", security.hash_password(PASSWORD), "viewer")
    conn.close()
    import_example(path)
    return path


def login(db_path, username):
    client = TestClient(app_for(db_path))
    response = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client, response.json()["csrf_token"]


# --- the files --------------------------------------------------------------------------------------------------------

def test_save_and_list_preset_roundtrip(db_path):
    folder = example_folder(db_path)
    flt = EventFilter(query="Webshell", phase=Phase.ANALYSIS, technique="T1059", since=date(2026, 10, 1))
    path = presets.save_preset(folder, "t1059-offen", "T1059 offen", flt)
    assert path == folder / "presets" / "t1059-offen.yaml"
    assert "Webshell" in path.read_text(encoding="utf-8")

    found, problems = presets.list_presets(folder)
    assert problems == []
    assert len(found) == 1
    preset = found[0]
    assert preset.name == "t1059-offen" and preset.label == "T1059 offen"
    assert preset.filter.query == "Webshell" and preset.filter.phase == Phase.ANALYSIS
    assert preset.filter.technique == "T1059" and preset.filter.since == date(2026, 10, 1)


def test_preset_name_cannot_leave_the_folder(db_path):
    folder = example_folder(db_path)
    for bad in ("../evil", "a/b", "A", "", "name mit leerzeichen", ".hidden"):
        with pytest.raises(presets.PresetRejected):
            presets.preset_path(folder, bad)
        with pytest.raises(presets.PresetRejected):
            presets.save_preset(folder, bad, None, EventFilter())
    assert not (folder / "evil.yaml").exists()


def test_existing_preset_name_is_refused(db_path):
    folder = example_folder(db_path)
    presets.save_preset(folder, "doppelt", None, EventFilter(query="x"))
    with pytest.raises(StoreError, match="existiert bereits"):
        presets.save_preset(folder, "doppelt", None, EventFilter(query="y"))


def test_the_loader_ignores_the_presets_folder(db_path):
    folder = example_folder(db_path)
    before = find_topic_files(folder)
    presets.save_preset(folder, "kein-topic", None, EventFilter(query="x"))
    assert find_topic_files(folder) == before  # the preset is not read as a topic


def test_a_broken_preset_is_a_problem_not_a_crash(db_path):
    folder = example_folder(db_path)
    presets.save_preset(folder, "ok", None, EventFilter(query="x"))
    (folder / "presets" / "kaputt.yaml").write_text("filter: [1, 2]\n", encoding="utf-8")
    (folder / "presets" / "falsch.yaml").write_text("filter:\n  technique: X1\n", encoding="utf-8")
    (folder / "presets" / "unbekannt.yaml").write_text("filter:\n  kaputt: 1\n", encoding="utf-8")
    (folder / "presets" / "link.yaml").symlink_to(folder / "timeline.yaml")
    found, problems = presets.list_presets(folder)
    assert [p.name for p in found] == ["ok"]
    assert len(problems) == 4
    assert any("Links" in p for p in problems)
    assert any("kaputt.yaml" in p for p in problems)
    assert any("ATT&CK" in p for p in problems)
    assert any("unbekannt.yaml" in p for p in problems)


# --- the pages --------------------------------------------------------------------------------------------------------

def test_event_list_applies_a_preset_and_a_parameter_overrides_it(db_path):
    folder = example_folder(db_path)
    presets.save_preset(folder, "nichts", None, EventFilter(query="gibt-es-nicht-zzz"))
    presets.save_preset(folder, "webshell", None, EventFilter(query="Webshell"))
    client, _ = login(db_path, "anna")

    response = client.get("/events?preset=nichts")
    assert response.status_code == 200 and "Keine Events passen" in response.text

    response = client.get("/events?preset=webshell")
    assert "Webshell im Upload-Verzeichnis gefunden" in response.text

    # an explicit query parameter wins over the preset's query
    response = client.get("/events?preset=nichts&q=Webshell")
    assert "Webshell im Upload-Verzeichnis gefunden" in response.text

    # the preset shows as selected in the dropdown
    assert 'value="webshell" selected' in client.get("/events?preset=webshell").text


def test_save_preset_via_post_writes_the_file(db_path):
    folder = example_folder(db_path)
    client, csrf = login(db_path, "anna")
    response = client.post("/events/presets", data={
        "csrf_token": csrf, "name": "ui-preset", "label": "UI-Preset", "q": "Webshell", "phase": "Analysis",
    }, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/events?preset=ui-preset"
    text = (folder / "presets" / "ui-preset.yaml").read_text(encoding="utf-8")
    assert "UI-Preset" in text and "Webshell" in text and "Analysis" in text
    found, problems = presets.list_presets(folder)
    assert problems == [] and [p.name for p in found] == ["ui-preset"]


def test_save_preset_needs_writer_role_and_csrf(db_path):
    client, csrf = login(db_path, "viewer")
    response = client.post("/events/presets", data={"csrf_token": csrf, "name": "x", "q": "y"}, follow_redirects=False)
    assert response.status_code == 403

    client, _ = login(db_path, "anna")
    response = client.post("/events/presets", data={"csrf_token": "falsch", "name": "x", "q": "y"}, follow_redirects=False)
    assert response.status_code == 403
    assert not (example_folder(db_path) / "presets").exists()


def test_duplicate_preset_name_shows_the_error_on_the_page(db_path):
    folder = example_folder(db_path)
    presets.save_preset(folder, "schon-da", None, EventFilter(query="x"))
    client, csrf = login(db_path, "anna")
    response = client.post("/events/presets", data={"csrf_token": csrf, "name": "schon-da", "q": "y"})
    assert response.status_code == 200
    assert "existiert bereits" in response.text
