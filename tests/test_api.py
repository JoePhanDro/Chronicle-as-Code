from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import app_for, import_example
from chronicle import accounts, security
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
    client = TestClient(app_for(db_path))
    response = client.post("/api/v1/auth/login", json={"username": "viewer", "password": PASSWORD})
    assert response.status_code == 200
    return client


@pytest.fixture
def client(db_path):
    return logged_in_client(db_path)


def test_health(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}  # no version: the endpoint is public (issue #261)


def test_health_discloses_no_version_even_when_logged_in(client):
    assert "version" not in client.get("/api/v1/health").json()


def test_swagger_redoc_and_openapi_are_not_served(db_path):
    # The docs pages pull their scripts from a CDN, against the vendor policy; they stay off (issue #261).
    client = TestClient(app_for(db_path))
    for path in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
        assert client.get(path).status_code == 404, path


def test_version_is_available_to_admins_only(db_path):
    from chronicle import __version__

    conn = connect(db_path)
    accounts.create_user(conn, "chef", security.hash_password(PASSWORD), "admin")
    accounts.create_user(conn, "anna", security.hash_password(PASSWORD), "analyst")
    conn.close()

    def login(username):
        client = TestClient(app_for(db_path))
        response = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
        assert response.status_code == 200
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        return client

    assert login("chef").get("/api/v1/admin/version").json() == {"version": __version__}
    assert login("anna").get("/api/v1/admin/version").status_code in (401, 403)
    assert TestClient(app_for(db_path)).get("/api/v1/admin/version").status_code in (401, 403)


def test_empty_database_has_no_topics(client):
    assert client.get("/api/v1/topics").json() == []
    assert client.get("/api/v1/events").json() == []


def test_unknown_topic_and_event_return_404(client):
    assert client.get("/api/v1/topics/nope").status_code == 404
    assert client.get("/api/v1/topics/nope/events").status_code == 404
    assert client.get("/api/v1/events/nope").status_code == 404


def test_import_sample_timeline_is_readable_via_api(db_path, client):
    (report,) = import_example(db_path)
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
    assert analysis["triggered_by"] == ["exploit"]
    assert analysis["topics"] == ["inc-2026-042"]
    assert [(item["file"], item["type"], item["mime"]) for item in analysis["evidence"]] == [
        ("evidence/waf-block.png", "screenshot", "image/png")
    ]


def test_reimport_of_the_same_file_does_not_duplicate(db_path, client):
    import_example(db_path)
    import_example(db_path)
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
