from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chronicle.api import create_app
from chronicle.importer import import_folder

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "chronicle.db"


@pytest.fixture
def client(db_path):
    return TestClient(create_app(db_path))


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

    client = TestClient(create_app(db_path))
    assert client.get("/api/v1/incidents").json() == []
