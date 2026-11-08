"""Incident-Klassifikation auf Topic-Ebene (issue #225): Modell, Datei, Merge, Anzeige, Berichte."""

import json

import pytest
from pydantic import ValidationError

from chronicle import repository
from chronicle.merge import MergeConflict, merge_texts
from chronicle.models import IncidentClassification, Timeline, Topic
from chronicle.store import dump_timeline, _read_model

TOPIC = "inc-2026-042"
PASSWORD = "correct horse battery"


def classification(**changes):
    data = {"severity": "high", "tlp": "AMBER", "impact": "ERP ausgefallen", "affected_systems": "erp, mail",
            "data_categories": "personenbezogene Daten", "affected_persons": 120, "cause": "Phishing",
            "measures": "Mails geblockt"}
    data.update(changes)
    return IncidentClassification.model_validate(data)


# --- the model -----------------------------------------------------------------------


def test_severity_and_tlp_are_fixed_levels():
    with pytest.raises(ValidationError):
        IncidentClassification(severity="sehr hoch")
    with pytest.raises(ValidationError):
        IncidentClassification(tlp="AMBER+STRICT")
    assert classification(severity="critical").severity == "critical"
    assert classification(tlp="RED").tlp == "RED"


def test_lists_are_sets_and_texts_are_bounded():
    inc = IncidentClassification(affected_systems="mail, erp, mail", data_categories="PII")
    assert inc.affected_systems == ["erp", "mail"]
    assert inc.data_categories == ["PII"]
    with pytest.raises(ValidationError):
        IncidentClassification(impact="x" * 2001)
    with pytest.raises(ValidationError):
        IncidentClassification(affected_persons=-1)
    with pytest.raises(ValidationError):
        IncidentClassification(cause="x", extra="feld")


def test_stored_leaves_empty_fields_out():
    assert IncidentClassification(severity="low").stored() == {"severity": "low"}
    assert IncidentClassification().stored() == {}


# --- file: old files stay byte-identical ----------------------------------------------


def test_topic_without_incident_gains_no_key(folder):
    from chronicle.db import connect
    conn = connect(folder.parent / "chronicle.db")
    topic = repository.get_topic(conn, TOPIC)
    values = {name: topic[name] for name in ("key", "kind", "summary", "reporting", "notifications")}
    # only the title changes; the file is valid afterwards and has no incident key (old files stay valid)
    repository.update_topic(conn, TOPIC, Topic.model_validate({**values, "title": "Umbenannt", "incident": None}),
                            None, "127.0.0.1", data_dir=folder)
    text = (folder / "timeline.yaml").read_text(encoding="utf-8")
    assert "Umbenannt" in text
    assert _read_model(text).topic.incident is None  # no incident key was added
    conn.close()


def test_incident_block_is_written_only_when_set(folder):
    from chronicle.db import connect
    conn = connect(folder.parent / "chronicle.db")
    topic = repository.get_topic(conn, TOPIC)
    values = {name: topic[name] for name in ("key", "title", "kind", "summary", "reporting", "notifications")}
    repository.update_topic(conn, TOPIC, Topic.model_validate({**values, "incident": {"severity": "high", "tlp": "RED"}}),
                            None, "127.0.0.1", data_dir=folder)
    text = (folder / "timeline.yaml").read_text(encoding="utf-8")
    assert "incident:" in text and "severity: high" in text and "tlp: RED" in text
    # roundtrip through the model: the index holds the same block
    assert repository.get_topic(conn, TOPIC)["incident"] == {"severity": "high", "tlp": "RED"}
    # removing the block removes the key from the file
    repository.update_topic(conn, TOPIC, Topic.model_validate({**values, "incident": None}), None, "127.0.0.1", data_dir=folder)
    assert _read_model((folder / "timeline.yaml").read_text(encoding="utf-8")).topic.incident is None
    conn.close()


# --- merge -----------------------------------------------------------------------------


def _topic_text(incident=None, **changes):
    data = {"key": "inc-1", "title": "Vorfall", "kind": "incident"}
    data.update(changes)
    if incident is not None:
        data["incident"] = incident
    return dump_timeline(Timeline(topic=Topic.model_validate(data)))


def test_different_incident_fields_merge_without_conflict():
    base = _topic_text()
    ours = _topic_text(incident={"severity": "high"})
    theirs = _topic_text(incident={"tlp": "RED", "cause": "Phishing"})
    merged = _read_model(merge_texts(base, ours, theirs))
    assert merged.topic.incident.severity == "high"
    assert merged.topic.incident.tlp == "RED"
    assert merged.topic.incident.cause == "Phishing"
    # the merged file holds the block with exactly the merged fields
    assert "incident:" in merge_texts(base, ours, theirs)


def test_same_incident_field_on_both_sides_is_a_conflict():
    base = _topic_text(incident={"severity": "low"})
    ours = _topic_text(incident={"severity": "high"})
    theirs = _topic_text(incident={"severity": "critical"})
    with pytest.raises(MergeConflict, match="topic.incident"):
        merge_texts(base, ours, theirs)


def test_reporting_and_notifications_merge_now_too():
    base = _topic_text()
    ours = _topic_text(reporting={"regulation": "nis2", "known_at": "2026-10-14T08:00:00+02:00"})
    theirs = _topic_text(notifications={"webhook": {"url": "https://hooks.example.com/x"}})
    merged = _read_model(merge_texts(base, ours, theirs))
    assert merged.topic.reporting.regulation == "nis2"
    assert merged.topic.notifications.webhook.url == "https://hooks.example.com/x"


# --- UI and API -------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def insecure_cookies(monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")


def _client(tmp_path, folder, role="analyst"):
    from fastapi.testclient import TestClient

    from chronicle import accounts, security
    from chronicle.api import create_app
    from chronicle.db import connect

    conn = connect(tmp_path / "chronicle.db")
    accounts.create_user(conn, "anna", security.hash_password(PASSWORD), role)
    conn.close()
    client = TestClient(create_app(tmp_path / "chronicle.db", data_dir=folder))
    response = client.post("/api/v1/auth/login", json={"username": "anna", "password": PASSWORD})
    assert response.status_code == 200
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client


def _post(client, path, **data):
    return client.post(path, data={"csrf_token": client.headers["X-CSRF-Token"], **data}, follow_redirects=False)


def test_form_roundtrip_and_display(tmp_path, folder):
    client = _client(tmp_path, folder, "admin")
    response = _post(client, f"/topics/{TOPIC}/edit", title="Incident 042", kind="incident", summary="",
                     rep_regulation="none", incident_severity="critical", incident_tlp="RED",
                     incident_impact="Alles kaputt", incident_systems="erp, mail", incident_data_categories="PII",
                     incident_persons="42", incident_cause="Phishing", incident_measures="Geblockt")
    assert response.status_code == 303, response.text

    topic = client.get(f"/api/v1/topics/{TOPIC}").json()
    assert topic["incident"] == {"severity": "critical", "tlp": "RED", "impact": "Alles kaputt",
                                 "affected_systems": ["erp", "mail"], "data_categories": ["PII"],
                                 "affected_persons": 42, "cause": "Phishing", "measures": "Geblockt"}

    page = client.get(f"/topics/{TOPIC}")
    assert "TLP:RED" in page.text and "kritisch" in page.text
    assert "Incident-Klassifikation" in page.text and "Phishing" in page.text

    listing = client.get("/topics")
    assert 'data-severity="critical"' in listing.text
    # the filter bar appears from six topics on
    for number in range(6):
        assert _post(client, "/topics/new", title=f"Weiteres {number}", kind="project").status_code == 303
    listing = client.get("/topics")
    assert 'id="topic-severity"' in listing.text


def test_empty_form_clears_the_block(tmp_path, folder):
    client = _client(tmp_path, folder, "admin")
    _post(client, f"/topics/{TOPIC}/edit", title="Incident 042", kind="incident", summary="", rep_regulation="none",
          incident_severity="high")
    assert client.get(f"/api/v1/topics/{TOPIC}").json()["incident"] == {"severity": "high"}
    _post(client, f"/topics/{TOPIC}/edit", title="Incident 042", kind="incident", summary="", rep_regulation="none")
    assert client.get(f"/api/v1/topics/{TOPIC}").json()["incident"] is None


def test_invalid_values_are_rejected_in_the_form(tmp_path, folder):
    client = _client(tmp_path, folder, "admin")
    response = _post(client, f"/topics/{TOPIC}/edit", title="Incident 042", kind="incident", summary="",
                     rep_regulation="none", incident_severity="katastrophal")
    assert response.status_code == 422
    response = _post(client, f"/topics/{TOPIC}/edit", title="Incident 042", kind="incident", summary="",
                     rep_regulation="none", incident_persons="viele")
    assert response.status_code == 422


def test_viewer_reads_but_does_not_change(tmp_path, folder):
    viewer = _client(tmp_path, folder, "viewer")
    response = _post(viewer, f"/topics/{TOPIC}/edit", title="X", incident_severity="high")
    assert response.status_code == 403
    assert viewer.get(f"/api/v1/topics/{TOPIC}").json()["incident"] is None


# --- reports ----------------------------------------------------------------------------


def test_pdf_header_has_classification_and_tlp_banner(tmp_path, folder):
    from chronicle.db import connect
    from chronicle.pdf_report import _classification, _header, _tlp_banner, render_pdf

    conn = connect(tmp_path / "chronicle.db")
    topic = repository.get_topic(conn, TOPIC)
    values = {name: topic[name] for name in ("key", "title", "kind", "summary", "reporting")}
    repository.update_topic(conn, TOPIC, Topic.model_validate({**values, "incident": {
        "severity": "critical", "tlp": "AMBER", "impact": "ERP down", "affected_persons": 7, "cause": "Phishing"}}),
        None, "127.0.0.1", data_dir=folder)
    conn.close()

    from chronicle.exporter import topic_document
    conn = connect(tmp_path / "chronicle.db")
    document, _ = topic_document(conn, TOPIC, expand_refs=True, data_dir=folder)
    timeline = Timeline.model_validate(document)
    conn.close()

    header_text = " ".join(item.getPlainText() for item in _header(timeline, "technisch", "2026-11-08 12:00 UTC")
                           if hasattr(item, "getPlainText"))
    assert "Klassifikation" in header_text
    cls = _classification(timeline.topic)
    cells = " ".join(cell.getPlainText() for row in cls[-1]._cellvalues for cell in row if hasattr(cell, "getPlainText"))
    assert "Schweregrad" in cells and "kritisch" in cells
    assert "TLP:AMBER" in cells and "Phishing" in cells and "7" in cells
    banner = _tlp_banner(timeline.topic)
    assert banner and "TLP:AMBER" in banner[0]._cellvalues[0][0].getPlainText()
    empty = Timeline.model_validate({"topic": {"key": "x", "title": "t"}}).topic
    assert not _classification(empty)
    assert not _tlp_banner(empty)

    pdf = render_pdf(timeline, {}, "technisch", "2026-11-08 12:00 UTC")
    assert pdf.startswith(b"%PDF")


def test_html_report_header_shows_classification(tmp_path, folder):
    from chronicle.db import connect

    conn = connect(tmp_path / "chronicle.db")
    topic = repository.get_topic(conn, TOPIC)
    values = {name: topic[name] for name in ("key", "title", "kind", "summary", "reporting")}
    repository.update_topic(conn, TOPIC, Topic.model_validate({**values, "incident": {"severity": "high", "tlp": "GREEN"}}),
                            None, "127.0.0.1", data_dir=folder)
    conn.close()
    client = _client(tmp_path, folder, "admin")  # the report API is admin-only
    response = client.get(f"/api/v1/topics/{TOPIC}/report")
    assert response.status_code == 200
    assert "TLP:GREEN" in response.text
    assert "Schweregrad:" in response.text and "hoch" in response.text


def test_exporter_roundtrip_keeps_incident(tmp_path, folder):
    from chronicle.db import connect
    from chronicle.exporter import topic_document

    conn = connect(tmp_path / "chronicle.db")
    topic = repository.get_topic(conn, TOPIC)
    values = {name: topic[name] for name in ("key", "title", "kind", "summary", "reporting")}
    repository.update_topic(conn, TOPIC, Topic.model_validate({**values, "incident": {"severity": "low", "cause": "Test"}}),
                            None, "127.0.0.1", data_dir=folder)
    document, _ = topic_document(conn, TOPIC, data_dir=folder)
    conn.close()
    assert document["topic"]["incident"] == {"severity": "low", "cause": "Test"}
    assert json.dumps(document["topic"]["incident"])  # JSON-serializable for the schema check
