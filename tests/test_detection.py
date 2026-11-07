"""The detection reference of an event (issue #257): which rule/alert in the SIEM/EDR led to it.

- the model: at least rule_id or alert_id, bounded fields, no unknown fields
- stored in the topic file (YAML round-trip) and in the index, checked by chronicle check
- shown on the event page and in the PDF report, filterable by rule_id (API and /events)
"""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from chronicle import accounts, repository, security
from chronicle.check import check_folder
from chronicle.db import connect
from chronicle.models import Detection, Event
from chronicle.repository import EventFilter
from tests.conftest import app_for, example_folder, import_example

PASSWORD = "correct horse battery"
RULE_ID = "sigma-webshell-upload-7f3a"  # the detection of the example event "analysis" in data/timeline.yaml


@pytest.fixture(autouse=True)
def insecure_cookies(monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "detection.db"
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
    return client


def event_dict(**changes):
    data = {"key": "ev-test", "t0": "2026-10-15T10:00:00+02:00", "phase": "Analysis", "actor": "Analyst",
            "action": "Test", "status": "open"}
    data.update(changes)
    return data


# --- the model --------------------------------------------------------------------------------------------------------

def test_detection_needs_rule_id_or_alert_id():
    with pytest.raises(ValidationError, match="rule_id oder alert_id"):
        Detection()
    assert Detection(rule_id="R-1").rule_id == "R-1"
    assert Detection(alert_id="alert-9").alert_id == "alert-9"


def test_detection_fields_are_bounded_and_stripped():
    with pytest.raises(ValidationError, match="200"):
        Detection(rule_id="x" * 201)
    with pytest.raises(ValidationError, match="200"):
        Detection(rule_id="R-1", source="  ")
    assert Detection(rule_id="  R-1  ").rule_id == "R-1"
    with pytest.raises(ValidationError):
        Detection(rule_id="R-1", bogus="x")


def test_event_roundtrips_through_yaml():
    from chronicle.store import dump_timeline, _read_model
    from chronicle.models import Timeline, Topic

    event = Event.model_validate(event_dict(detection={"rule_id": RULE_ID, "source": "Defender"}))
    text = dump_timeline(Timeline(topic=Topic(key="t", title="T", kind="incident"), events=[event]))
    assert "detection:" in text and RULE_ID in text
    loaded = _read_model(text)
    assert loaded.events[0].detection.rule_id == RULE_ID
    assert loaded.events[0].detection.source == "Defender"


# --- storage, check, filter -------------------------------------------------------------------------------------------

def test_example_data_carries_the_detection_and_check_passes(db_path):
    assert check_folder(example_folder(db_path)) == []
    conn = connect(db_path)
    event = repository.get_event(conn, "analysis")
    assert event["detection"]["rule_id"] == RULE_ID
    assert event["detection"]["alert_id"] == "alert-2026-10-14-0042"
    conn.close()


def test_a_broken_detection_fails_the_check(db_path, tmp_path):
    folder = example_folder(db_path)
    (folder / "kaputt.yaml").write_text(
        "topic:\n  key: kaputt\n  title: K\nevents:\n  - key: k1\n    t0: 2026-10-14T10:00:00+02:00\n"
        "    phase: Analysis\n    actor: X\n    action: A\n    status: open\n    detection:\n      rule_name: ohne ID\n",
        encoding="utf-8",
    )
    problems = check_folder(folder)
    assert any("rule_id oder alert_id" in p for p in problems)


def test_filter_by_rule_id_finds_only_that_event(db_path):
    conn = connect(db_path)
    assert [e["key"] for e in repository.find_events(conn, EventFilter(rule=RULE_ID))] == ["analysis"]
    assert [e["key"] for e in repository.find_events(conn, EventFilter(rule=RULE_ID.upper()))] == ["analysis"]
    assert repository.find_events(conn, EventFilter(rule="gibt-es-nicht")) == []
    conn.close()


def test_create_event_with_detection_writes_it_to_the_file(db_path):
    folder = example_folder(db_path)
    conn = connect(db_path)
    event = Event.model_validate(event_dict(
        detection={"rule_id": "R-9", "rule_name": "Regel Neun", "rule_version": "v3", "source": "Elastic",
                   "alert_id": "a-1"}))
    created = repository.create_event(conn, event, ["inc-2026-042"], 1, "127.0.0.1", folder)
    assert created["detection"]["rule_id"] == "R-9"
    text = (folder / "timeline.yaml").read_text(encoding="utf-8")
    assert "detection:" in text and "R-9" in text and "Elastic" in text
    conn.close()


# --- pages and API ----------------------------------------------------------------------------------------------------

def test_api_filters_by_rule_and_returns_the_detection(db_path):
    client = login(db_path)
    found = client.get(f"/api/v1/events?rule={RULE_ID}").json()
    assert [e["key"] for e in found] == ["analysis"]
    assert found[0]["detection"]["rule_name"] == "Webshell Upload (Sigma)"
    one = client.get("/api/v1/events/analysis").json()
    assert one["detection"]["source"] == "Defender for Endpoint"


def test_event_page_shows_the_detection(db_path):
    client = login(db_path, "anna")
    response = client.get("/events/analysis")
    assert response.status_code == 200
    assert "Erkannt durch" in response.text and RULE_ID in response.text
    # the rule filter link points to the filtered list
    assert f"/events?rule={RULE_ID}" in response.text


def test_pdf_report_names_the_detection(db_path):
    from pypdf import PdfReader
    import io

    client = login(db_path)
    response = client.get("/api/v1/topics/inc-2026-042/report.pdf")
    assert response.status_code == 200
    text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(response.content)).pages)
    assert "Erkannt durch" in text and RULE_ID in text
