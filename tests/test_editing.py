from datetime import UTC, datetime
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chronicle import accounts, security
from chronicle.api import create_app
from chronicle.db import connect
from chronicle.importer import import_folder

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PASSWORD = "correct horse battery"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


@pytest.fixture(autouse=True)
def insecure_cookies(monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "edit.db"
    conn = connect(path)
    accounts.create_user(conn, "admin", security.hash_password(PASSWORD), "admin")
    accounts.create_user(conn, "anna", security.hash_password(PASSWORD), "analyst")
    accounts.create_user(conn, "viewer", security.hash_password(PASSWORD), "viewer")
    conn.close()
    import_folder(path, DATA_DIR)
    return path


def api_login(db_path, username):
    client = TestClient(create_app(db_path))
    response = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client


def writable(current, **changes):
    """The body a client sends back after GET: only the fields the write API accepts."""
    body = {name: current[name] for name in ("key", "t0", "phase", "actor", "system", "action",
                                              "status", "triggered_by", "details")}
    body.update({"topic": current["home_topic"], "topics": current["topics"], "version": current["version"]})
    body.update(changes)
    return body


def new_event(key="neu-1", **overrides):
    body = {
        "key": key,
        "t0": "2026-10-15T10:00:00+02:00",
        "phase": "Analysis",
        "actor": "Analyst",
        "action": "Neue Beobachtung",
        "status": "open",
        "topic": "inc-2026-042",
        "topics": [],
    }
    body.update(overrides)
    return body


def test_viewer_cannot_write(db_path):
    viewer = api_login(db_path, "viewer")
    assert viewer.post("/api/v1/events", json=new_event()).status_code == 403
    assert viewer.delete("/api/v1/events/exploit").status_code == 403


def test_analyst_creates_event_and_it_is_listed(db_path):
    anna = api_login(db_path, "anna")
    response = anna.post("/api/v1/events", json=new_event())
    assert response.status_code == 201
    assert response.json()["version"] == 1
    assert response.json()["source"] == "manual"
    assert response.json()["topics"] == ["inc-2026-042"]
    assert len(anna.get("/api/v1/topics/inc-2026-042/events").json()) == 8


def test_api_generates_key_when_none_is_sent(db_path):
    anna = api_login(db_path, "anna")
    body = new_event()
    del body["key"]
    response = anna.post("/api/v1/events", json=body)
    assert response.status_code == 201
    assert re.fullmatch(r"ev-\d{8}-[0-9a-f]{8}", response.json()["key"])
    assert anna.post("/api/v1/events", json={**body, "key": "bad key!"}).status_code == 422


def test_duplicate_key_is_conflict(db_path):
    anna = api_login(db_path, "anna")
    assert anna.post("/api/v1/events", json=new_event("exploit")).status_code == 409


def test_unknown_topic_and_trigger_are_rejected(db_path):
    anna = api_login(db_path, "anna")
    assert anna.post("/api/v1/events", json=new_event(topic="gibt-es-nicht")).status_code == 404
    assert anna.post("/api/v1/events", json=new_event(triggered_by="gibt-es-nicht")).status_code == 422


def test_update_with_stale_version_is_conflict(db_path):
    anna = api_login(db_path, "anna")
    current = anna.get("/api/v1/events/analysis").json()
    body = writable(current, action="geändert")
    assert anna.put("/api/v1/events/analysis", json=body).status_code == 200

    stale = anna.put("/api/v1/events/analysis", json=body)  # same version again
    assert stale.status_code == 409
    assert anna.get("/api/v1/events/analysis").json()["version"] == current["version"] + 1


def test_save_without_changes_creates_no_revision(db_path):
    anna = api_login(db_path, "anna")
    current = anna.get("/api/v1/events/analysis").json()
    saved = anna.put("/api/v1/events/analysis", json=writable(current))
    assert saved.status_code == 200
    assert saved.json()["version"] == current["version"]


def test_update_can_add_and_remove_topics_but_keeps_home(db_path):
    anna = api_login(db_path, "anna")
    current = anna.get("/api/v1/events/exploit").json()
    body = writable(current, topics=[])
    response = anna.put("/api/v1/events/exploit", json=body)
    assert response.status_code == 200
    assert response.json()["topics"] == ["inc-2026-042"]  # home stays linked


def test_delete_blocked_while_another_event_depends_on_it(db_path):
    anna = api_login(db_path, "anna")
    response = anna.delete("/api/v1/events/feed-cve")  # siem-alert and exploit are triggered by it
    assert response.status_code == 409
    assert "siem-alert" in response.json()["detail"]


def test_deleted_event_is_not_recreated_by_import(db_path):
    anna = api_login(db_path, "anna")
    assert anna.delete("/api/v1/events/lessons").status_code == 204
    assert anna.get("/api/v1/events/lessons").status_code == 404

    (report,) = import_folder(db_path, DATA_DIR)
    assert ("lessons", "in der Oberfläche gelöscht") in report.skipped
    assert anna.get("/api/v1/events/lessons").status_code == 404


def test_manually_edited_event_is_not_overwritten_by_import(db_path):
    anna = api_login(db_path, "anna")
    current = anna.get("/api/v1/events/analysis").json()
    body = writable(current, action="Von Hand korrigiert")
    assert anna.put("/api/v1/events/analysis", json=body).status_code == 200

    (report,) = import_folder(db_path, DATA_DIR)
    assert ("analysis", "in der Oberfläche bearbeitet") in report.skipped
    assert anna.get("/api/v1/events/analysis").json()["action"] == "Von Hand korrigiert"


def test_import_keeps_links_added_in_the_ui(db_path, tmp_path):
    anna = api_login(db_path, "anna")
    # Create a second topic through a new import file, then link an existing event to it in the UI.
    second = tmp_path / "second"
    second.mkdir()
    (second / "timeline.yaml").write_text(
        "topic:\n  key: projekt-x\n  title: Projekt X\n  kind: project\nevents: []\nrefs: []\n"
    )
    import_folder(db_path, second)
    current = anna.get("/api/v1/events/containment").json()
    body = writable(current, topics=["projekt-x"])
    assert anna.put("/api/v1/events/containment", json=body).status_code == 200

    import_folder(db_path, DATA_DIR)
    assert set(anna.get("/api/v1/events/containment").json()["topics"]) == {"inc-2026-042", "projekt-x"}


def test_every_change_is_audited_without_content(db_path):
    anna = api_login(db_path, "anna")
    anna.post("/api/v1/events", json=new_event("audit-1"))
    anna.delete("/api/v1/events/audit-1")
    admin = api_login(db_path, "admin")
    actions = [entry["action"] for entry in admin.get("/api/v1/audit").json()]
    assert "event_created" in actions
    assert "event_deleted" in actions
    entries = admin.get("/api/v1/audit").json()
    assert all("Neue Beobachtung" not in str(entry) for entry in entries)


def login_html(client, username):
    page = client.get("/login").text
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    response = client.post("/login", data={"username": username, "password": PASSWORD, "csrf_token": csrf})
    assert response.status_code in (200, 303)


def form_csrf(client):
    return client.get("/api/v1/auth/me").json()["csrf_token"]


def test_html_create_edit_delete_flow(db_path, monkeypatch):
    monkeypatch.setattr("chronicle.pages.new_event_key", lambda: "form-1")
    client = TestClient(create_app(db_path))
    login_html(client, "anna")
    assert client.get("/events").status_code == 200
    assert "Neues Event" in client.get("/events").text

    form = {
        "csrf_token": form_csrf(client), "t0_date": "2026-10-15", "t0_hour": "09", "t0_minute": "00", "t0_zone": "Europe/Berlin",
        "phase": "Analysis", "actor": "Analyst", "system": "", "action": "Über Formular",
        "status": "open", "triggered_by": "", "details": "**fett**",
        "topic": "inc-2026-042", "topics": ["inc-2026-042"],
    }
    created = client.post("/events/new", data=form, follow_redirects=False)
    assert created.status_code == 303
    assert created.headers["location"] == "/events/form-1"
    detail = client.get("/events/form-1").text
    assert "Über Formular" in detail

    upload = client.post(
        "/events/form-1/evidence",
        data={"csrf_token": form_csrf(client), "type": "screenshot", "description": "Probe"},
        files={"file": ("probe.png", PNG, "image/png")},
        follow_redirects=False,
    )
    assert upload.status_code == 303
    assert "probe.png" in client.get("/events/form-1").text

    version = client.get("/api/v1/events/form-1").json()["version"]
    edit = {**form, "action": "Geändert im Formular", "version": str(version)}
    edit.pop("topic")
    edit["csrf_token"] = form_csrf(client)
    edited = client.post("/events/form-1/edit", data=edit, follow_redirects=False)
    assert edited.status_code == 303
    assert client.get("/api/v1/events/form-1").json()["action"] == "Geändert im Formular"

    deleted = client.post("/events/form-1/delete", data={"csrf_token": form_csrf(client)}, follow_redirects=False)
    assert deleted.status_code == 303
    assert client.get("/api/v1/events/form-1").status_code == 404


def test_html_rejects_missing_csrf_and_shows_validation_errors(db_path):
    client = TestClient(create_app(db_path))
    login_html(client, "anna")
    base = {"key": "x-1", "t0_date": "2026-10-15", "t0_hour": "09", "t0_minute": "00", "t0_zone": "Europe/Berlin", "phase": "Analysis", "actor": "A",
            "action": "B", "status": "open", "topic": "inc-2026-042", "topics": []}

    assert client.post("/events/new", data={**base, "csrf_token": "falsch"}).status_code == 403

    invalid = client.post("/events/new", data={**base, "csrf_token": form_csrf(client), "t0_date": "2026-02-31"})
    assert invalid.status_code == 422
    assert "Das Datum ist ungültig" in invalid.text
    assert client.get("/api/v1/events/x-1").status_code == 404


def test_html_new_event_gets_generated_key(db_path):
    client = TestClient(create_app(db_path))
    login_html(client, "anna")
    assert 'name="key"' not in client.get("/events/new").text

    form = {
        "csrf_token": form_csrf(client), "t0_date": "2026-10-15", "t0_hour": "09", "t0_minute": "00", "t0_zone": "Europe/Berlin",
        "phase": "Analysis", "actor": "Analyst", "system": "", "action": "Ohne Key",
        "status": "open", "triggered_by": "", "details": "",
        "topic": "inc-2026-042", "topics": ["inc-2026-042"],
    }
    created = client.post("/events/new", data=form, follow_redirects=False)
    assert created.status_code == 303
    key = created.headers["location"].removeprefix("/events/")
    assert re.fullmatch(r"ev-\d{8}-[0-9a-f]{8}", key)
    assert client.get(f"/api/v1/events/{key}").json()["action"] == "Ohne Key"


def test_html_viewer_sees_list_but_no_write_pages(db_path):
    client = TestClient(create_app(db_path))
    login_html(client, "viewer")
    assert client.get("/events").status_code == 200
    assert "Neues Event" not in client.get("/events").text
    assert client.get("/events/new").status_code == 403
    assert client.post("/events/exploit/delete", data={"csrf_token": form_csrf(client)}).status_code == 403


def test_html_pages_redirect_to_login_without_session(db_path):
    client = TestClient(create_app(db_path))
    response = client.get("/events", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_html_new_event_is_stored_in_utc_with_attachments(db_path, monkeypatch):
    monkeypatch.setattr("chronicle.pages.new_event_key", lambda: "files-1")
    client = TestClient(create_app(db_path))
    login_html(client, "anna")
    page = client.get("/events/new").text
    assert 'type="date"' in page and 'name="t0_zone"' in page
    assert '<option value="Activity"' in page
    assert 'name="triggered_by" value="siem-alert"' in page  # the trigger is picked from existing events

    created = client.post(
        "/events/new",
        data={
            "csrf_token": form_csrf(client), "t0_date": "2026-10-15", "t0_hour": "09",
            "t0_minute": "30", "t0_zone": "Europe/Berlin", "phase": "Activity", "actor": "Anna", "system": "",
            "action": "Mit Anhängen", "status": "open", "triggered_by": "siem-alert", "details": "",
            "topic": "inc-2026-042", "topics": [], "evidence_type": "screenshot",
        },
        files=[("files", ("one.png", PNG, "image/png")), ("files", ("two.png", PNG, "image/png"))],
        follow_redirects=False,
    )
    assert created.status_code == 303, created.text

    stored = client.get("/api/v1/events/files-1").json()
    assert datetime.fromisoformat(stored["t0"]) == datetime(2026, 10, 15, 7, 30, tzinfo=UTC)
    assert stored["phase"] == "Activity"
    assert stored["triggered_by"] == ["siem-alert"]
    assert [item["file"] for item in stored["evidence"]] == ["one.png", "two.png"]


def test_html_bad_attachment_writes_no_event(db_path, monkeypatch):
    monkeypatch.setattr("chronicle.pages.new_event_key", lambda: "files-2")
    client = TestClient(create_app(db_path))
    login_html(client, "anna")
    rejected = client.post(
        "/events/new",
        data={
            "csrf_token": form_csrf(client), "t0_date": "2026-10-15", "t0_hour": "09",
            "t0_minute": "30", "t0_zone": "Europe/Berlin", "phase": "Activity", "actor": "Anna", "system": "",
            "action": "Falsche Datei", "status": "open", "triggered_by": "", "details": "",
            "topic": "inc-2026-042", "topics": [], "evidence_type": "screenshot",
        },
        files=[("files", ("kein-bild.png", b"kein png", "image/png"))],
        follow_redirects=False,
    )
    assert rejected.status_code == 422
    assert "kein-bild.png" in rejected.text
    assert client.get("/api/v1/events/files-2").status_code == 404


def test_html_edit_form_shows_stored_time_in_berlin(db_path, monkeypatch):
    monkeypatch.setattr("chronicle.pages.new_event_key", lambda: "zeit-1")
    client = TestClient(create_app(db_path))
    login_html(client, "anna")
    created = client.post("/events/new", data={
        "csrf_token": form_csrf(client), "t0_date": "2026-01-15", "t0_hour": "09",
        "t0_minute": "05", "t0_zone": "UTC", "phase": "Analysis", "actor": "A", "action": "Winterzeit",
        "status": "open", "triggered_by": "", "details": "", "topic": "inc-2026-042", "topics": [],
    }, follow_redirects=False)
    assert created.status_code == 303

    page = client.get("/events/zeit-1").text
    # Stored as 09:05 UTC; the form shows the same instant in Europe/Berlin (UTC+1 in January).
    assert '<option value="10" selected>' in page
    assert '<option value="05" selected>' in page
    assert '<option value="Europe/Berlin" selected>' in page
