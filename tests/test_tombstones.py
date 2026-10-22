"""Deleted events: tombstones with their last version, restore and purge (issue #34)."""

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from chronicle import accounts, repository, security
from chronicle.api import create_app
from chronicle.db import connect
from chronicle.importer import import_folder

PASSWORD = "correct horse battery"
ADMIN_PASSWORD = "admin-passwort-123"
IP = "127.0.0.1"


@pytest.fixture(autouse=True)
def insecure_cookies(monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")


def write_timelines(folder: Path) -> None:
    (folder / "evidence").mkdir(parents=True)
    (folder / "evidence" / "protokoll.txt").write_text("Protokollauszug\n", encoding="utf-8")
    topic_a = {
        "topic": {"key": "a", "title": "Topic A", "kind": "incident"},
        "events": [
            {"key": "e1", "t0": "2026-10-14T08:00:00+02:00", "phase": "Identification", "actor": "SIEM",
             "action": "Alarm eins", "status": "open", "details": "Details **fett**",
             "evidence": [{"file": "evidence/protokoll.txt", "type": "log", "description": "Protokoll"}]},
            {"key": "e2", "t0": "2026-10-14T09:00:00+02:00", "phase": "Analysis", "actor": "CSIRT",
             "action": "Analyse", "status": "in_progress"},
            {"key": "e4", "t0": "2026-10-14T10:00:00+02:00", "phase": "Containment", "actor": "CSIRT",
             "action": "Folgeereignis", "status": "open", "triggered_by": "e2"},
        ],
    }
    topic_b = {
        "topic": {"key": "b", "title": "Topic B", "kind": "other"},
        "events": [
            {"key": "e3", "t0": "2026-10-14T11:00:00+02:00", "phase": "Activity", "actor": "Team",
             "action": "Anderes Topic", "status": "resolved"},
        ],
    }
    (folder / "a.yaml").write_text(yaml.safe_dump(topic_a, allow_unicode=True), encoding="utf-8")
    (folder / "b.yaml").write_text(yaml.safe_dump(topic_b, allow_unicode=True), encoding="utf-8")


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "tombstones.db"
    conn = connect(path)
    accounts.create_user(conn, "chef", security.hash_password(ADMIN_PASSWORD), "admin")
    accounts.create_user(conn, "anna", security.hash_password(PASSWORD), "analyst")
    accounts.create_user(conn, "viewer", security.hash_password(PASSWORD), "viewer")
    conn.close()
    folder = tmp_path / "data"
    folder.mkdir()
    write_timelines(folder)
    import_folder(path, folder)
    return path


def user_id(conn, username):
    return accounts.get_user_by_name(conn, username)["id"]


def login(db_path, username, password=PASSWORD):
    client = TestClient(create_app(db_path))
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client


def test_restored_event_comes_back_with_details_links_and_evidence(db):
    conn = connect(db)
    anna = user_id(conn, "anna")
    before = repository.get_event(conn, "e1")

    repository.delete_event(conn, "e1", anna, IP)
    assert repository.get_event(conn, "e1") is None
    event, dropped = repository.restore_event(conn, "e1", anna, IP)

    assert dropped == []
    assert event["action"] == before["action"]
    assert event["details"] == before["details"]
    assert event["topics"] == ["a"]
    assert event["version"] == before["version"] + 1
    [item] = event["evidence"]
    assert repository.get_evidence_content(conn, item["id"])["content"] == b"Protokollauszug\n"
    assert repository.list_tombstones(conn) == []
    conn.close()


def test_restore_leaves_out_topics_and_triggers_that_are_gone(db):
    conn = connect(db)
    anna = user_id(conn, "anna")
    repository.link_event(conn, "b", "e4", anna, IP)
    repository.delete_event(conn, "e4", anna, IP)
    repository.delete_event(conn, "e2", anna, IP)  # e4 was its only dependent, and it is gone now
    repository.delete_event(conn, "e3", anna, IP)  # home events must go before their topic
    repository.delete_topic(conn, "b", anna, IP)

    event, dropped = repository.restore_event(conn, "e4", anna, IP)

    assert event["triggered_by"] is None
    assert event["topics"] == ["a"]
    assert dropped == ["Topic b", "Auslöser e2"]
    conn.close()


def test_restore_needs_the_home_topic(db):
    conn = connect(db)
    anna = user_id(conn, "anna")
    for key in ("e4", "e2", "e1"):
        repository.delete_event(conn, key, anna, IP)
    repository.delete_topic(conn, "a", anna, IP)

    with pytest.raises(repository.Conflict, match="Heimat-Topic"):
        repository.restore_event(conn, "e1", anna, IP)
    conn.close()


def test_tombstone_from_before_snapshots_can_only_be_purged(db):
    conn = connect(db)
    anna = user_id(conn, "anna")
    with conn:
        conn.execute("INSERT INTO deleted_events (key) VALUES ('alt')")

    [tombstone] = [item for item in repository.list_tombstones(conn) if item["key"] == "alt"]
    assert tombstone["restorable"] is False
    with pytest.raises(repository.Conflict, match="keine letzte Fassung"):
        repository.restore_event(conn, "alt", anna, IP)

    repository.purge_tombstone(conn, "alt", anna, IP)
    assert repository.list_tombstones(conn) == []
    conn.close()


def test_purge_removes_the_tombstone_and_its_evidence_copy(db):
    conn = connect(db)
    anna = user_id(conn, "anna")
    repository.delete_event(conn, "e1", anna, IP)
    [tombstone] = repository.list_tombstones(conn)
    assert tombstone["evidence"] == 1

    repository.purge_tombstone(conn, "e1", anna, IP)

    assert repository.list_tombstones(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM deleted_evidence").fetchone()[0] == 0
    conn.close()


def test_purged_event_is_imported_again(db):
    conn = connect(db)
    anna = user_id(conn, "anna")
    repository.delete_event(conn, "e1", anna, IP)
    repository.purge_tombstone(conn, "e1", anna, IP)
    conn.close()

    [report] = [item for item in import_folder(db, db.parent / "data") if item.topic == "a"]
    assert "e1" in report.created


def test_restored_event_is_treated_like_any_imported_event(db):
    conn = connect(db)
    anna = user_id(conn, "anna")
    repository.delete_event(conn, "e1", anna, IP)
    repository.restore_event(conn, "e1", anna, IP)
    conn.close()

    [report] = [item for item in import_folder(db, db.parent / "data") if item.topic == "a"]
    assert "e1" not in [key for key, _ in report.skipped]
    assert "e1" in report.updated


def test_admin_page_is_for_admins_only(db):
    assert login(db, "viewer").get("/admin/grabsteine").status_code == 403
    assert login(db, "anna").get("/admin/grabsteine").status_code == 403
    assert login(db, "chef", ADMIN_PASSWORD).get("/admin/grabsteine").status_code == 200


def test_admin_restores_and_purges_from_the_page(db):
    anna = login(db, "anna")
    assert anna.delete("/api/v1/events/e1").status_code == 204
    chef = login(db, "chef", ADMIN_PASSWORD)
    assert "e1" in chef.get("/admin/grabsteine").text

    token = chef.headers["X-CSRF-Token"]
    forbidden = chef.post("/admin/grabsteine/e1/wiederherstellen", data={"csrf_token": "falsch"}, follow_redirects=False)
    assert forbidden.status_code == 403
    restored = chef.post("/admin/grabsteine/e1/wiederherstellen", data={"csrf_token": token}, follow_redirects=False)
    assert restored.status_code == 303
    assert chef.get("/api/v1/events/e1").status_code == 200

    assert anna.delete("/api/v1/events/e1").status_code == 204
    purged = chef.post("/admin/grabsteine/e1/entfernen", data={"csrf_token": token}, follow_redirects=False)
    assert purged.status_code == 303
    assert "Keine gelöschten Events" in chef.get("/admin/grabsteine").text


def test_audit_log_names_restore_and_purge(db):
    anna = login(db, "anna")
    anna.delete("/api/v1/events/e1")
    conn = connect(db)
    anna_id = user_id(conn, "anna")
    repository.restore_event(conn, "e1", anna_id, IP)
    repository.delete_event(conn, "e1", anna_id, IP)
    repository.purge_tombstone(conn, "e1", anna_id, IP)
    actions = [row["action"] for row in conn.execute("SELECT action FROM audit_log ORDER BY pk").fetchall()]
    conn.close()
    assert "event_restored" in actions
    assert "tombstone_purged" in actions


def test_database_from_before_snapshots_gets_the_new_columns(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE deleted_events (key TEXT PRIMARY KEY, deleted_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00Z')")
    old.execute("INSERT INTO deleted_events (key) VALUES ('alt')")
    old.commit()
    old.close()

    conn = connect(path)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(deleted_events)").fetchall()}
    assert "snapshot" in columns
    assert [item["key"] for item in repository.list_tombstones(conn)] == ["alt"]
    conn.close()
