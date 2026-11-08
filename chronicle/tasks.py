"""Tasks (checklist items) of a topic (issue #226, ADR 0031). The topic file is written first, the index follows (ADR 0008)."""

from __future__ import annotations

import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import accounts, checklists
from .errors import Conflict, Invalid, NotFound
from .folder_writes import all_or_nothing, file_of_topic, recorded_version, store_change
from .index import reindex
from .models import Task
from .queries import get_topic
from .store import set_tasks_in_file


def _tasks_of(conn: sqlite3.Connection, topic_key: str) -> list[Task]:
    topic = get_topic(conn, topic_key)
    if topic is None:
        raise NotFound(f"Topic {topic_key!r} nicht gefunden")
    return [Task.model_validate(item) for item in topic["tasks"]]


def _write_tasks(conn: sqlite3.Connection, topic_key: str, tasks: list[Task], data_dir: Path,
                 actor_id: int, ip: str, action: str, detail: str) -> None:
    path = file_of_topic(conn, topic_key)
    with all_or_nothing(data_dir):
        with store_change(path, "Die Änderung wurde nicht gespeichert"):
            set_tasks_in_file(data_dir / path, tasks, expected_version=recorded_version(conn, path))
        reindex(conn, data_dir)
    accounts.audit(conn, action, user_id=actor_id, target=topic_key, detail=detail, ip=ip)


def create_task(conn: sqlite3.Connection, topic_key: str, task: Task, actor_id: int, ip: str, data_dir: Path) -> dict:
    tasks = _tasks_of(conn, topic_key)
    if any(item.key == task.key for item in tasks):
        raise Conflict(f"Task-Key {task.key!r} ist in diesem Topic bereits vergeben")
    _write_tasks(conn, topic_key, [*tasks, task], data_dir, actor_id, ip, "task_created", task.key)
    return task.model_dump(mode="json")


def update_task(conn: sqlite3.Connection, topic_key: str, task_key: str, task: Task, actor_id: int, ip: str,
                data_dir: Path) -> dict:
    """Replace one task (e.g. check it off). The key in the path and in the body must match."""
    if task.key != task_key:
        raise Invalid("Der Key im Pfad und im Body müssen übereinstimmen")
    tasks = _tasks_of(conn, topic_key)
    if not any(item.key == task_key for item in tasks):
        raise NotFound(f"Task {task_key!r} nicht gefunden")
    changed = [name for name in Task.model_fields if getattr(task, name) != getattr(next(i for i in tasks if i.key == task_key), name)]
    _write_tasks(conn, topic_key, [task if item.key == task_key else item for item in tasks], data_dir,
                 actor_id, ip, "task_updated", f"{task_key}:{','.join(changed) if changed else 'keine Änderung'}")
    return task.model_dump(mode="json")


def delete_task(conn: sqlite3.Connection, topic_key: str, task_key: str, actor_id: int, ip: str, data_dir: Path) -> None:
    tasks = _tasks_of(conn, topic_key)
    if not any(item.key == task_key for item in tasks):
        raise NotFound(f"Task {task_key!r} nicht gefunden")
    _write_tasks(conn, topic_key, [item for item in tasks if item.key != task_key], data_dir,
                 actor_id, ip, "task_deleted", task_key)


def apply_template(conn: sqlite3.Connection, topic_key: str, template_name: str, actor_id: int, ip: str,
                   data_dir: Path) -> int:
    """Copy the tasks of a checklist template into the topic with fresh keys (issue #226). Existing tasks stay.
    `deadline_hours` of a template task becomes an absolute deadline from now. Returns the number added."""
    try:
        template = checklists.get_checklist(data_dir, template_name)
    except checklists.ChecklistRejected as exc:
        raise Invalid(str(exc)) from exc
    tasks = _tasks_of(conn, topic_key)
    existing = {item.key for item in tasks}
    now = datetime.now(UTC)
    added = 0
    for item in template.tasks:
        key = f"task-{secrets.token_hex(4)}"
        while key in existing:
            key = f"task-{secrets.token_hex(4)}"
        existing.add(key)
        deadline = now + timedelta(hours=item.deadline_hours) if item.deadline_hours else None
        tasks.append(Task(key=key, title=item.title, status="open", assignee=item.assignee,
                          deadline=deadline, note=item.note))
        added += 1
    _write_tasks(conn, topic_key, tasks, data_dir, actor_id, ip, "template_applied", f"{template_name}:{added}")
    return added
