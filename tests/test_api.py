from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chronicle import accounts, security
from chronicle.api import create_app
from chronicle.db import connect
from chronicle.importer import import_folder

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PASSWORD = "correct horse battery"


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "chronicle.db"


@pytest.fixture(autouse=True)
def insecure_cookies_for_http_tests(monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")


def logged_in_client(db_path):
    conn = connect(db_path)
    accounts.create_user(conn, "viewer", security.hash_password(PASSWORD), "viewer")
    conn.close()
    client = TestClient(create_app(db_path))
    response = client.post("/api/v1/auth/login", json={"username": "viewer", "password": PASSWORD})
    assert response.status_code == 200
    return client


@pytest.fixture
def client(db_path):
    return logged_in_client(db_path)


def test_health(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_empty_database_has_no_incidents(client):
    assert client.get("/api/v1/incidents").json() == []


def test_unknown_incident_returns_404(client):
    assert client.get("/api/v1/incidents/999").status_code == 404
    assert client.get("/api/v1/incidents/999/events").status_code == 404


def test_import_sample_timeline_is_readable_via_api(db_path, client):
    incident_id = import_folder(db_path, DATA_DIR)

    incidents = client.get("/api/v1/incidents").json()
    assert [item["id"] for item in incidents] == [incident_id]
    assert incidents[0]["title"].startswith("INC-2026-042")

    events = client.get(f"/api/v1/incidents/{incident_id}/events").json()
    assert len(events) == 7
    assert [event["event_id"] for event in events][0] == "feed-cve"  # earliest first

    analysis = next(event for event in events if event["event_id"] == "analysis")
    assert analysis["triggered_by"] == "exploit"
    assert analysis["evidence"] == [
        {"file": "evidence/waf-block.svg", "type": "screenshot", "description": "WAF blockiert nachträglichen Zugriff auf die Webshell"}
    ]


def test_import_is_stored_per_incident(db_path):
    first = import_folder(db_path, DATA_DIR)
    second = import_folder(db_path, DATA_DIR)
    assert first != second


def test_import_fails_on_missing_evidence_without_writing(tmp_path, db_path):
    import yaml

    data = tmp_path / "data"
    data.mkdir()
    (data / "timeline.yaml").write_text(
        yaml.safe_dump(
            {
                "title": "T",
                "events": [
                    {
                        "t0": "2026-10-14T08:00:00+02:00",
                        "phase": "Analysis",
                        "actor": "A",
                        "action": "x",
                        "status": "open",
                        "evidence": [{"file": "missing.png", "type": "screenshot"}],
                    }
                ],
            }
        )
    )
    from chronicle.loader import TimelineError

    with pytest.raises(TimelineError):
        import_folder(db_path, data)

    client = logged_in_client(db_path)
    assert client.get("/api/v1/incidents").json() == []
