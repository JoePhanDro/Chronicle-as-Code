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


def test_empty_database_has_no_topics(client):
    assert client.get("/api/v1/topics").json() == []
    assert client.get("/api/v1/events").json() == []


def test_unknown_topic_and_event_return_404(client):
    assert client.get("/api/v1/topics/nope").status_code == 404
    assert client.get("/api/v1/topics/nope/events").status_code == 404
    assert client.get("/api/v1/events/nope").status_code == 404


def test_import_sample_timeline_is_readable_via_api(db_path, client):
    report = import_folder(db_path, DATA_DIR)
    assert report.topic == "inc-2026-042"
    assert len(report.created) == 7

    topics = client.get("/api/v1/topics").json()
    assert [item["key"] for item in topics] == ["inc-2026-042"]
    assert topics[0]["kind"] == "incident"
    assert topics[0]["event_count"] == 7

    events = client.get("/api/v1/topics/inc-2026-042/events").json()
    assert len(events) == 7
    assert events[0]["key"] == "feed-cve"  # earliest first

    analysis = client.get("/api/v1/events/analysis").json()
    assert analysis["triggered_by"] == "exploit"
    assert analysis["topics"] == ["inc-2026-042"]
    assert [(item["file"], item["type"], item["mime"]) for item in analysis["evidence"]] == [
        ("evidence/waf-block.png", "screenshot", "image/png")
    ]


def test_reimport_of_the_same_file_does_not_duplicate(db_path, client):
    import_folder(db_path, DATA_DIR)
    import_folder(db_path, DATA_DIR)
    assert len(client.get("/api/v1/topics").json()) == 1
    assert len(client.get("/api/v1/events").json()) == 7


def test_import_fails_on_missing_evidence_without_writing(tmp_path, db_path):
    import yaml

    data = tmp_path / "data"
    data.mkdir()
    (data / "timeline.yaml").write_text(
        yaml.safe_dump(
            {
                "topic": {"key": "t-1", "title": "T"},
                "events": [
                    {
                        "key": "ev-1",
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
    assert client.get("/api/v1/topics").json() == []
