"""File mode (ADR 0008, issue #89): an edit in the app is written to the topic file, then the index follows the file."""


import pytest
import yaml

from chronicle import repository
from chronicle.models import Event
from chronicle.repository import Conflict


def edited(conn, key, **changes):
    current = repository.get_event(conn, key)
    data = {field: current[field] for field in ("key", "t0", "phase", "actor", "system", "action", "status",
                                                "triggered_by", "details")}
    data.update(changes)
    return current, Event.model_validate(data)


def test_an_edit_is_written_to_the_file_and_the_index_follows(folder, conn):
    before_text = (folder / "timeline.yaml").read_text(encoding="utf-8")
    current, event = edited(conn, "analysis", action="Analyse abgeschlossen")

    updated = repository.update_event(conn, "analysis", event, current["topics"], current["version"],
                                      actor_id=None, ip="test", data_dir=folder)

    text = (folder / "timeline.yaml").read_text(encoding="utf-8")
    assert {item["key"]: item for item in yaml.safe_load(text)["events"]}["analysis"]["action"] == "Analyse abgeschlossen"
    assert text.startswith(before_text.splitlines()[0])  # the comments at the top of the file are kept
    assert "# Evidence-Pfade sind relativ zum Datenordner." in text
    assert updated["action"] == "Analyse abgeschlossen"
    assert updated["version"] == current["version"] + 1


def test_a_save_without_changes_writes_nothing(folder, conn):
    before = (folder / "timeline.yaml").read_bytes()
    current, event = edited(conn, "analysis")

    repository.update_event(conn, "analysis", event, current["topics"], current["version"],
                            actor_id=None, ip="test", data_dir=folder)

    assert (folder / "timeline.yaml").read_bytes() == before


def test_a_change_made_outside_the_app_is_refused(folder, conn):
    path = folder / "timeline.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("Webshell im Upload-Verzeichnis", "von Hand"), encoding="utf-8")
    edited_by_hand = path.read_bytes()
    current, event = edited(conn, "analysis", action="App-Änderung")

    with pytest.raises(Conflict, match="außerhalb"):
        repository.update_event(conn, "analysis", event, current["topics"], current["version"],
                                actor_id=None, ip="test", data_dir=folder)

    assert path.read_bytes() == edited_by_hand  # the app did not overwrite the change
    assert repository.get_event(conn, "analysis")["action"] != "App-Änderung"


@pytest.fixture(autouse=True)
def insecure_cookies_for_http_tests(monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")  # the test client talks plain HTTP


def body_from(current, **changes):
    body = {field: current[field] for field in ("key", "t0", "phase", "actor", "system", "action", "status",
                                                "triggered_by", "details", "version")}
    body.update(topic=current["home_topic"], topics=current["topics"], **changes)
    return body


def test_api_edit_is_written_to_the_file_and_answered_with_the_new_state(tmp_path, folder, api_client):
    client = api_client()
    current = client.get("/api/v1/events/analysis").json()

    response = client.put("/api/v1/events/analysis", json=body_from(current, action="über die API geändert"))

    assert response.status_code == 200, response.text
    assert response.json()["action"] == "über die API geändert"
    events = yaml.safe_load((folder / "timeline.yaml").read_text(encoding="utf-8"))["events"]
    assert {item["key"]: item for item in events}["analysis"]["action"] == "über die API geändert"


def test_api_edit_after_an_outside_change_is_a_conflict(tmp_path, folder, api_client):
    client = api_client()
    current = client.get("/api/v1/events/analysis").json()
    path = folder / "timeline.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "\n# von Hand angehängt\n", encoding="utf-8")

    response = client.put("/api/v1/events/analysis", json=body_from(current, action="App"))

    assert response.status_code == 409
    assert "# von Hand angehängt" in path.read_text(encoding="utf-8")


def test_api_topic_create_with_a_broken_folder_answers_422_and_writes_nothing(tmp_path, folder, api_client):
    client = api_client()
    (folder / "kaputt.yaml").write_text("topic: [kaputt\n", encoding="utf-8")

    response = client.post("/api/v1/topics", json={"key": "neu", "title": "Neu", "kind": "project"})

    assert response.status_code == 422, response.text
    assert not (folder / "neu.yaml").exists()


def test_api_event_edit_with_a_broken_folder_answers_422_and_changes_no_file(tmp_path, folder, api_client):
    client = api_client()
    current = client.get("/api/v1/events/analysis").json()
    (folder / "kaputt.yaml").write_text("topic: [kaputt\n", encoding="utf-8")
    before = (folder / "timeline.yaml").read_bytes()

    response = client.put("/api/v1/events/analysis", json=body_from(current, action="wird nicht gespeichert"))

    assert response.status_code == 422, response.text
    assert (folder / "timeline.yaml").read_bytes() == before


def test_api_link_is_written_into_the_topic_file(tmp_path, folder, api_client):
    client = api_client()
    created = client.post("/api/v1/topics", json={"key": "b", "title": "B", "kind": "project"})
    assert created.status_code == 201, created.text

    response = client.post("/api/v1/topics/b/events", json={"event": "lessons"})

    assert response.status_code == 201, response.text
    assert yaml.safe_load((folder / "b.yaml").read_text(encoding="utf-8"))["refs"] == ["lessons"]
