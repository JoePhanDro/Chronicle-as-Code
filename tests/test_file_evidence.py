"""File mode (ADR 0008, issue #89): attachments are files in evidence/ and the topic file names them."""


import pytest
import yaml

from chronicle import repository
from chronicle.db import connect
from chronicle.evidence import check_file
from chronicle.models import Event
from chronicle.repository import Conflict, Invalid, NotFound

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
OTHER_PNG = b"\x89PNG\r\n\x1a\n" + b"\x01" * 24


def attach(conn, folder, key, name, data, evidence_type="screenshot", description=""):
    return repository.add_evidence(conn, key, check_file(name, data), evidence_type, description,
                                   actor_id=None, ip="test", data_dir=folder)


def evidence_of(folder, key):
    document = yaml.safe_load((folder / "timeline.yaml").read_text(encoding="utf-8"))
    return next(item for item in document["events"] if item["key"] == key).get("evidence", [])


def test_an_attachment_is_a_file_in_evidence_and_named_in_the_topic_file(folder, conn):
    meta = attach(conn, folder, "analysis", "neu.png", PNG, description="Probe")

    assert (folder / "evidence" / "analysis" / "neu.png").read_bytes() == PNG
    assert evidence_of(folder, "analysis")[-1] == {"file": "evidence/analysis/neu.png", "type": "screenshot",
                                                   "description": "Probe"}
    assert meta["file"] == "evidence/analysis/neu.png"
    assert repository.get_event(conn, "analysis")["version"] == 2


def test_a_different_file_with_the_same_name_gets_a_number(folder, conn):
    attach(conn, folder, "analysis", "neu.png", PNG)
    attach(conn, folder, "analysis", "neu.png", OTHER_PNG)

    assert (folder / "evidence" / "analysis" / "neu-2.png").read_bytes() == OTHER_PNG
    assert (folder / "evidence" / "analysis" / "neu.png").read_bytes() == PNG


def test_the_same_file_twice_is_refused_and_nothing_is_written(folder, conn):
    attach(conn, folder, "analysis", "neu.png", PNG)
    before = (folder / "timeline.yaml").read_bytes()

    with pytest.raises(Invalid, match="bereits angehängt"):
        attach(conn, folder, "analysis", "neu.png", PNG)
    assert (folder / "timeline.yaml").read_bytes() == before
    assert not (folder / "evidence" / "analysis" / "neu-2.png").exists()


def test_an_unknown_event_is_refused(folder, conn):
    with pytest.raises(NotFound):
        attach(conn, folder, "gibt-es-nicht", "neu.png", PNG)


def test_an_unknown_type_is_refused(folder, conn):
    with pytest.raises(Invalid, match="Evidence-Typ"):
        attach(conn, folder, "analysis", "neu.png", PNG, evidence_type="video")


def test_a_change_outside_the_app_refuses_the_attachment_and_removes_its_file(folder, conn):
    path = folder / "timeline.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "\n# von Hand\n", encoding="utf-8")

    with pytest.raises(Conflict, match="außerhalb"):
        attach(conn, folder, "analysis", "neu.png", PNG)
    assert not (folder / "evidence" / "analysis" / "neu.png").exists()


def test_deleting_an_attachment_removes_its_entry_and_file(folder, conn):
    meta = attach(conn, folder, "analysis", "neu.png", PNG)

    repository.delete_evidence(conn, meta["id"], actor_id=None, ip="test", data_dir=folder)

    assert "evidence/analysis/neu.png" not in [item["file"] for item in evidence_of(folder, "analysis")]
    assert not (folder / "evidence" / "analysis" / "neu.png").exists()


def test_an_attachment_survives_an_edit_of_its_event(folder, conn):
    attach(conn, folder, "analysis", "neu.png", PNG)
    current = repository.get_event(conn, "analysis")
    data = {k: current[k] for k in ("key", "t0", "phase", "actor", "system", "action", "status", "triggered_by",
                                    "details")}
    data["action"] = "geändert"
    repository.update_event(conn, "analysis", Event.model_validate(data), current["topics"], current["version"],
                            actor_id=None, ip="test", data_dir=folder)

    assert "evidence/analysis/neu.png" in [item["file"] for item in evidence_of(folder, "analysis")]
    assert any(item["file"] == "evidence/analysis/neu.png" for item in repository.get_event(conn, "analysis")["evidence"])


def test_the_api_uploads_and_deletes_an_attachment_in_file_mode(tmp_path, folder, monkeypatch):
    from fastapi.testclient import TestClient

    from chronicle import accounts, security
    from chronicle.api import create_app

    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")
    setup = connect(tmp_path / "chronicle.db")
    accounts.create_user(setup, "anna", security.hash_password("correct horse battery"), "analyst")
    setup.close()
    client = TestClient(create_app(tmp_path / "chronicle.db", data_dir=folder))
    response = client.post("/api/v1/auth/login", json={"username": "anna", "password": "correct horse battery"})
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]

    uploaded = client.post("/api/v1/events/analysis/evidence", files={"file": ("api.png", PNG, "image/png")},
                           data={"type": "screenshot", "description": ""})
    assert uploaded.status_code == 201, uploaded.text
    assert (folder / "evidence" / "analysis" / "api.png").read_bytes() == PNG

    deleted = client.delete(f"/api/v1/evidence/{uploaded.json()['id']}")
    assert deleted.status_code == 204, deleted.text
    assert not (folder / "evidence" / "analysis" / "api.png").exists()
