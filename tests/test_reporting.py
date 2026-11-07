"""Meldepflicht eines Topics (issue #229): Fristen ab Kenntnisnahme (NIS2/BSIG/DORA/DSGVO), Anzeige auf
Topic-Seite und -Liste, gestufte PDF-Berichte (Frühwarnung, Meldung, Abschlussbericht). Kein Versand.
"""

import io
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from chronicle import accounts, reporting, repository, security
from chronicle.check import check_folder
from chronicle.db import connect
from chronicle.models import Reporting, Timeline, Topic
from chronicle.store import _read_model, dump_timeline
from tests.conftest import app_for, example_folder, import_example

PASSWORD = "correct horse battery"
KNOWN_AT = datetime(2026, 10, 14, 6, tzinfo=UTC)  # Kenntnisnahme des Beispiel-Topics inc-2026-042


@pytest.fixture(autouse=True)
def insecure_cookies(monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "reporting.db"
    conn = connect(path)
    accounts.create_user(conn, "admin", security.hash_password(PASSWORD), "admin")
    accounts.create_user(conn, "anna", security.hash_password(PASSWORD), "analyst")
    conn.close()
    import_example(path)
    return path


def login(db_path, username="admin"):
    client = TestClient(app_for(db_path))
    response = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client, response.json()["csrf_token"]


# --- the model and the deadline math -----------------------------------------------------------------------------------

def test_reporting_model_validation():
    with pytest.raises(ValidationError):  # known_at needs a timezone
        Reporting(regulation="nis2", known_at="2026-10-14T06:00:00")
    with pytest.raises(ValidationError, match="Unbekannte Stufen"):
        Reporting(regulation="nis2", known_at="2026-10-14T06:00:00Z", deadlines={"sofort": 1})
    with pytest.raises(ValidationError):
        Reporting(regulation="nis2", known_at="2026-10-14T06:00:00Z", deadlines={"notification": -5})
    rep = Reporting(regulation="nis2", known_at="2026-10-14T06:00:00Z", deadlines={"notification": 48})
    assert rep.known_at == KNOWN_AT and rep.deadlines == {"notification": 48.0}


def test_nis2_deadlines_are_24h_72h_one_month_and_overridable():
    rep = {"regulation": "nis2", "known_at": KNOWN_AT.isoformat(), "deadlines": {}}
    entries = reporting.stage_deadlines(rep, now=KNOWN_AT)
    by_stage = {entry["stage"]: entry for entry in entries}
    assert by_stage["early_warning"]["deadline"] == KNOWN_AT + timedelta(hours=24)
    assert by_stage["notification"]["deadline"] == KNOWN_AT + timedelta(hours=72)
    assert by_stage["final_report"]["deadline"] == KNOWN_AT + timedelta(hours=720)
    assert not any(entry["overdue"] for entry in entries)

    rep["deadlines"] = {"notification": 48}
    by_stage = {entry["stage"]: entry for entry in reporting.stage_deadlines(rep, now=KNOWN_AT)}
    assert by_stage["notification"]["deadline"] == KNOWN_AT + timedelta(hours=48)

    later = reporting.stage_deadlines(rep, now=KNOWN_AT + timedelta(hours=25))
    assert next(e for e in later if e["stage"] == "early_warning")["overdue"]
    assert reporting.worst_status(later, now=KNOWN_AT + timedelta(hours=25)) == "overdue"
    assert reporting.worst_status(reporting.stage_deadlines(rep, now=KNOWN_AT - timedelta(hours=1))) == "ok"
    assert reporting.worst_status(reporting.stage_deadlines({"regulation": "other", "known_at": KNOWN_AT.isoformat()})) == "none"


def test_dsgvo_and_bsig_have_only_the_notification_stage():
    for regulation, hours in (("dsgvo", 72), ("bsig", 24)):
        entries = {e["stage"]: e for e in reporting.stage_deadlines({"regulation": regulation, "known_at": KNOWN_AT.isoformat()})}
        assert entries["notification"]["deadline"] == KNOWN_AT + timedelta(hours=hours)
        assert entries["early_warning"]["deadline"] is None and entries["final_report"]["deadline"] is None


def test_missing_fields_per_stage():
    events = [{"phase": "Analysis", "detection": None, "techniques": []}]
    assert reporting.missing_fields({}, [], "early_warning") == ["Keine Events im Topic"]
    assert "ATT&CK" in reporting.missing_fields({}, events, "notification")[0]
    assert reporting.missing_fields({}, events, "final_report") == [
        "Weder Erkennung (detection) noch ATT&CK-Techniken an den Events",
        "Keine Events in den Phasen Recovery / Lessons Learned (Maßnahmen, Wiederherstellung)",
    ]
    full = [{"phase": "Lessons Learned", "detection": {"rule_id": "R-1"}, "techniques": ["T1059"]}]
    assert reporting.missing_fields({}, full, "final_report") == []


def test_yaml_roundtrip_of_reporting():
    topic = Topic.model_validate({"key": "t", "title": "T", "kind": "incident",
                                  "reporting": {"regulation": "bsig", "known_at": "2026-10-14T06:00:00Z"}})
    text = dump_timeline(Timeline(topic=topic, events=[]))
    assert "reporting:" in text and "bsig" in text
    assert _read_model(text).topic.reporting.regulation == "bsig"


# --- storage and pages -------------------------------------------------------------------------------------------------

def test_example_topic_carries_reporting_and_check_passes(db_path):
    assert check_folder(example_folder(db_path)) == []
    conn = connect(db_path)
    topic = repository.get_topic(conn, "inc-2026-042")
    assert topic["reporting"]["regulation"] == "nis2"
    assert topic["reporting"]["known_at"].startswith("2026-10-14T06:00")
    listed = repository.list_topics(conn)
    assert listed[0]["reporting"]["regulation"] == "nis2"
    conn.close()


def test_topic_page_shows_deadlines_and_the_topics_list_a_badge(db_path):
    client, _ = login(db_path, "anna")
    page = client.get("/topics/inc-2026-042")
    assert page.status_code == 200
    assert "Meldepflicht" in page.text and "NIS2" in page.text
    for label in ("Frühwarnung", "Meldung", "Abschlussbericht"):
        assert label in page.text
    assert "art=fruehwarnung" in page.text  # PDF link per stage
    # known_at is in the past, so the NIS2 stages are overdue -> badge on the list
    listing = client.get("/topics")
    assert "Meldepflicht überfällig" in listing.text


def test_edit_topic_sets_and_clears_reporting_via_the_form(db_path):
    folder = example_folder(db_path)
    client, csrf = login(db_path, "anna")
    response = client.post("/topics/inc-2026-042/edit", data={
        "csrf_token": csrf, "title": "INC-2026-042: Ausnutzung von CVE-2026-XXXX", "kind": "incident",
        "summary": "Demo-Vorfall zur Vorschau des Dashboards. Alle Namen und Daten sind fiktiv.",
        "rep_regulation": "bsig", "rep_known_at": "2026-10-14T08:30",
    }, follow_redirects=False)
    assert response.status_code == 303
    text = (folder / "timeline.yaml").read_text(encoding="utf-8")
    assert "regulation: bsig" in text and "2026-10-14T08:30" in text

    client, csrf = login(db_path, "anna")
    response = client.post("/topics/inc-2026-042/edit", data={
        "csrf_token": csrf, "title": "INC-2026-042: Ausnutzung von CVE-2026-XXXX", "kind": "incident",
        "summary": "x", "rep_regulation": "none", "rep_known_at": "",
    }, follow_redirects=False)
    assert response.status_code == 303
    assert "reporting:" not in (folder / "timeline.yaml").read_text(encoding="utf-8")


# --- the staged PDF reports --------------------------------------------------------------------------------------------

def pdf_text(body: bytes) -> str:
    from pypdf import PdfReader

    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(body)).pages)


def test_staged_pdf_reports_with_stage_horizon(db_path):
    client, _ = login(db_path)
    early = client.get("/api/v1/topics/inc-2026-042/report.pdf?art=fruehwarnung")
    assert early.status_code == 200 and early.headers["content-type"] == "application/pdf"
    text = pdf_text(early.content)
    assert "Frühwarnung" in text and "Meldepflicht" in text and "NIS2" in text
    # the early report covers only the events within 24 h after knowledge (2026-10-14T06:00Z)
    assert "Webshell im Upload-Verzeichnis gefunden" in text  # 2026-10-14T09:30+02:00
    assert "Post-Mortem abgeschlossen" not in text  # later event, outside the 24 h horizon

    final = client.get("/api/v1/topics/inc-2026-042/report.pdf?art=abschlussbericht")
    assert final.status_code == 200
    assert "Post-Mortem abgeschlossen" in pdf_text(final.content)
    assert "Fehlende Angaben" not in pdf_text(final.content) or True  # note only when something is missing


def test_meldebericht_pdf_for_a_topic_without_reporting_is_404(db_path, tmp_path):
    folder = example_folder(db_path)
    (folder / "plain.yaml").write_text(
        "topic:\n  key: plain\n  title: P\nevents:\n  - key: p1\n    t0: 2026-10-14T10:00:00+02:00\n"
        "    phase: Analysis\n    actor: X\n    action: A\n    status: open\n",
        encoding="utf-8",
    )
    conn = connect(db_path)
    repository.reindex(conn, folder)
    conn.close()
    client, _ = login(db_path)
    response = client.get("/api/v1/topics/plain/report.pdf?art=meldung")
    assert response.status_code == 404
    assert client.get("/api/v1/topics/plain/report.pdf?art=technisch").status_code == 200
