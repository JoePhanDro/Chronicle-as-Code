"""Aufgaben/Checklisten pro Topic und Vorlagen (issue #226, ADR 0031)."""

import itertools
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from chronicle import checklists, repository
from chronicle.merge import MergeConflict, merge_texts
from chronicle.models import Task, Timeline, Topic
from chronicle.store import dump_timeline, _read_model

TOPIC = "inc-2026-042"
PASSWORD = "correct horse battery"


def task(key="task-1", **changes):
    data = {"key": key, "title": "Systeme isolieren", "status": "open"}
    data.update(changes)
    return Task.model_validate(data)


@pytest.fixture(autouse=True)
def insecure_cookies(monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")


def _client(tmp_path, folder, role="analyst"):
    from fastapi.testclient import TestClient

    from chronicle import accounts, security
    from chronicle.api import create_app
    from chronicle.db import connect

    name = f"user{next(_client.counter)}"
    conn = connect(tmp_path / "chronicle.db")
    accounts.create_user(conn, name, security.hash_password(PASSWORD), role)
    conn.close()
    client = TestClient(create_app(tmp_path / "chronicle.db", data_dir=folder))
    response = client.post("/api/v1/auth/login", json={"username": name, "password": PASSWORD})
    assert response.status_code == 200
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client


_client.counter = itertools.count()


def _post(client, path, **data):
    return client.post(path, data={"csrf_token": client.headers["X-CSRF-Token"], **data}, follow_redirects=False)


def _tasks(client):
    """The tasks of the example topic via the API (the topic payload itself carries no tasks, issue #226)."""
    return client.get(f"/api/v1/topics/{TOPIC}/tasks").json()


# --- model and templates -----------------------------------------------------------------


def test_task_model_validation():
    with pytest.raises(ValidationError):
        task(title="")
    with pytest.raises(ValidationError):
        task(status="alles")
    with pytest.raises(ValidationError):
        task(deadline="2026-10-20T18:00:00")  # no timezone
    done = task(status="done", assignee=" anna ", deadline="2026-10-20T18:00:00+02:00", note="a\r\nb")
    assert done.assignee == "anna" and done.note == "a\nb"
    assert done.deadline == datetime(2026, 10, 20, 16, 0, tzinfo=UTC)


def test_timeline_rejects_duplicate_task_keys():
    with pytest.raises(ValidationError, match="task"):
        Timeline(topic=Topic.model_validate({"key": "t", "title": "T"}), tasks=[task("a"), task("a")])


def test_checklist_template_loading(tmp_path):
    folder = tmp_path / "data"
    folder.mkdir()
    (folder / "checklists").mkdir()
    (folder / "checklists" / "ransomware.yaml").write_text(
        "label: Ransomware\ntasks:\n  - {title: Isolieren, deadline_hours: 4}\n  - {title: Backups}\n", encoding="utf-8")
    (folder / "checklists" / "broken.yaml").write_text("tasks: []\n", encoding="utf-8")
    templates, problems = checklists.list_checklists(folder)
    assert [t.name for t in templates] == ["ransomware"]
    assert templates[0].label == "Ransomware" and len(templates[0].tasks) == 2
    assert problems and "broken" in problems[0]
    assert checklists.get_checklist(folder, "ransomware").tasks[0].deadline_hours == 4
    with pytest.raises(checklists.ChecklistRejected):
        checklists.get_checklist(folder, "../evil")
    # the loader must not read the checklists folder as topics
    from chronicle.loader import TimelineError, find_topic_files
    with pytest.raises(TimelineError):
        find_topic_files(folder)


# --- file: old files stay byte-identical ---------------------------------------------------


def test_old_files_gain_no_tasks_key(folder):
    from chronicle.db import connect
    path = folder / "timeline.yaml"
    conn = connect(folder.parent / "chronicle.db")
    topic = repository.get_topic(conn, TOPIC)
    updated = Topic.model_validate({**{name: topic[name] for name in ("key", "title", "kind", "summary", "reporting",
                                                                      "notifications", "incident")},
                                    "title": "Umbenannt"})
    repository.update_topic(conn, TOPIC, updated, None, "127.0.0.1", data_dir=folder)
    conn.close()
    text = path.read_text(encoding="utf-8")
    assert "Umbenannt" in text
    assert _read_model(text).tasks == []  # no tasks key was added


def test_task_roundtrip_through_the_file(folder):
    from chronicle.db import connect
    conn = connect(folder.parent / "chronicle.db")
    created = repository.create_task(conn, TOPIC, task("task-a", deadline="2026-10-20T18:00:00+02:00"),
                                     None, "127.0.0.1", data_dir=folder)
    assert created["status"] == "open"
    text = (folder / "timeline.yaml").read_text(encoding="utf-8")
    assert "tasks:" in text and "task-a" in text
    stored = _read_model(text).tasks
    assert [t.key for t in stored] == ["task-a"] and stored[0].deadline is not None
    # check it off
    repository.update_task(conn, TOPIC, "task-a", task("task-a", status="done"), None, "127.0.0.1", data_dir=folder)
    assert repository.get_topic(conn, TOPIC)["tasks"][0]["status"] == "done"
    # delete removes the key
    repository.delete_task(conn, TOPIC, "task-a", None, "127.0.0.1", data_dir=folder)
    assert "tasks" not in (folder / "timeline.yaml").read_text(encoding="utf-8")
    assert repository.get_topic(conn, TOPIC)["tasks"] == []
    conn.close()


# --- merge --------------------------------------------------------------------------------


def _text_with_tasks(tasks):
    return dump_timeline(Timeline(topic=Topic.model_validate({"key": "inc-1", "title": "V"}), tasks=tasks))


def test_both_sides_checking_off_different_tasks_merges_cleanly():
    base = _text_with_tasks([task("a"), task("b")])
    ours = _text_with_tasks([task("a", status="done"), task("b")])
    theirs = _text_with_tasks([task("a"), task("b", status="done")])
    merged = _read_model(merge_texts(base, ours, theirs)).tasks
    assert {t.key: t.status.value for t in merged} == {"a": "done", "b": "done"}


def test_delete_and_change_of_the_same_task_is_a_conflict():
    base = _text_with_tasks([task("a")])
    ours = _text_with_tasks([])  # deleted
    theirs = _text_with_tasks([task("a", status="done")])  # changed
    with pytest.raises(MergeConflict, match="task a"):
        merge_texts(base, ours, theirs)


# --- API and pages --------------------------------------------------------------------------


def test_task_crud_via_api(tmp_path, folder):
    client = _client(tmp_path, folder)
    response = client.post(f"/api/v1/topics/{TOPIC}/tasks",
                           json={"key": "task-api", "title": "Eins", "status": "open", "assignee": "anna",
                                 "deadline": "2026-10-20T18:00:00+02:00", "note": ""})
    assert response.status_code == 201, response.text
    response = client.put(f"/api/v1/topics/{TOPIC}/tasks/task-api",
                          json={"key": "task-api", "title": "Eins", "status": "done", "assignee": "anna",
                                "deadline": "2026-10-20T18:00:00+02:00", "note": ""})
    assert response.status_code == 200
    assert _tasks(client)[0]["status"] == "done"
    assert client.delete(f"/api/v1/topics/{TOPIC}/tasks/task-api").status_code == 204
    assert _tasks(client) == []
    # duplicate key -> 409, unknown task -> 404, key mismatch -> 422
    client.post(f"/api/v1/topics/{TOPIC}/tasks", json={"key": "task-x", "title": "A"})
    assert client.post(f"/api/v1/topics/{TOPIC}/tasks", json={"key": "task-x", "title": "B"}).status_code == 409
    assert client.put(f"/api/v1/topics/{TOPIC}/tasks/nein", json={"key": "nein", "title": "A"}).status_code == 404
    assert client.put(f"/api/v1/topics/{TOPIC}/tasks/task-x", json={"key": "anderer", "title": "A"}).status_code == 422


def test_template_apply_via_api_and_page(tmp_path, folder):
    client = _client(tmp_path, folder)
    # the example data ships one template
    response = client.post(f"/api/v1/topics/{TOPIC}/tasks/from-template", json={"template": "ransomware"})
    assert response.status_code == 201, response.text
    assert response.json()["added"] == 5
    tasks = _tasks(client)
    assert len(tasks) == 5
    assert any(t["deadline"] for t in tasks)  # deadline_hours became an absolute deadline
    # applying again adds fresh keys, existing tasks stay
    assert client.post(f"/api/v1/topics/{TOPIC}/tasks/from-template", json={"template": "ransomware"}).json()["added"] == 5
    assert len(_tasks(client)) == 10
    assert client.post(f"/api/v1/topics/{TOPIC}/tasks/from-template", json={"template": "gibts-nicht"}).status_code == 422

    # page: create, check off via checkbox, delete; flash after template apply
    response = _post(client, f"/topics/{TOPIC}/tasks/new", title="Neue Aufgabe", assignee="", deadline="", note="")
    assert response.status_code == 303 and response.headers["location"] == f"/topics/{TOPIC}?tasks=1"
    page = client.get(f"/topics/{TOPIC}")
    assert "Neue Aufgabe" in page.text and "Aufgaben" in page.text
    key = next(t["key"] for t in _tasks(client) if t["title"] == "Neue Aufgabe")
    assert _post(client, f"/topics/{TOPIC}/tasks/{key}/status", status="done").status_code == 303
    assert next(t for t in _tasks(client) if t["key"] == key)["status"] == "done"
    assert _post(client, f"/topics/{TOPIC}/tasks/{key}/delete").status_code == 303
    assert all(t["key"] != key for t in _tasks(client))
    response = _post(client, f"/topics/{TOPIC}/tasks/from-template", template="ransomware")
    assert response.headers["location"] == f"/topics/{TOPIC}?tasks=5"


def test_task_roles(tmp_path, folder):
    viewer = _client(tmp_path, folder, "viewer")
    assert viewer.post(f"/api/v1/topics/{TOPIC}/tasks", json={"key": "t", "title": "x"}).status_code == 403
    assert _tasks(viewer) == []  # reads fine
    assert _post(viewer, f"/topics/{TOPIC}/tasks/new", title="x").status_code == 403


def test_overview_badge_and_pdf(tmp_path, folder):
    from chronicle.db import connect

    client = _client(tmp_path, folder)
    client.post(f"/api/v1/topics/{TOPIC}/tasks", json={"key": "task-a", "title": "A"})
    client.post(f"/api/v1/topics/{TOPIC}/tasks", json={"key": "task-b", "title": "B"})
    client.put(f"/api/v1/topics/{TOPIC}/tasks/task-b",
               json={"key": "task-b", "title": "B", "status": "done"})
    listing = client.get("/topics")
    assert "1 offene Aufgabe" in listing.text

    conn = connect(tmp_path / "chronicle.db")
    from chronicle.exporter import topic_document
    from chronicle.pdf_report import _tasks_section
    from chronicle.models import Timeline as TL
    document, _ = topic_document(conn, TOPIC, data_dir=folder)
    conn.close()
    timeline = TL.model_validate(document)
    assert len(timeline.tasks) == 2
    texts = " ".join(cell.getPlainText() for row in _tasks_section(timeline)[-1]._cellvalues for cell in row
                     if hasattr(cell, "getPlainText"))
    assert "Aufgaben" in " ".join(f.getPlainText() for f in _tasks_section(timeline) if hasattr(f, "getPlainText"))
    assert "task-a" not in texts and "A" in texts and "offen" in texts
    # export keeps the tasks
    assert document["tasks"][0]["key"] == "task-a"


def test_audit_entries(tmp_path, folder):
    from chronicle.db import connect
    client = _client(tmp_path, folder)
    client.post(f"/api/v1/topics/{TOPIC}/tasks", json={"key": "task-a", "title": "A"})
    client.put(f"/api/v1/topics/{TOPIC}/tasks/task-a", json={"key": "task-a", "title": "A", "status": "done"})
    client.post(f"/api/v1/topics/{TOPIC}/tasks/from-template", json={"template": "ransomware"})
    client.delete(f"/api/v1/topics/{TOPIC}/tasks/task-a")
    with connect(tmp_path / "chronicle.db") as conn:
        actions = [row[0] for row in conn.execute(
            "SELECT action FROM ops.audit_log WHERE action LIKE 'task_%' OR action = 'template_applied' ORDER BY pk")]
    assert actions == ["task_created", "task_updated", "template_applied", "task_deleted"]
