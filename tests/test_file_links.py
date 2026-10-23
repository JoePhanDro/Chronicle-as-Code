"""File mode (ADR 0008, issue #89): links between topics live under refs in the other topic's file."""

import shutil
from pathlib import Path

import pytest
import yaml

from chronicle import repository
from chronicle.db import connect
from chronicle.importer import import_folder
from chronicle.models import Event
from chronicle.repository import NotFound

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture
def folder(tmp_path):
    copy = tmp_path / "data"
    shutil.copytree(DATA_DIR, copy)
    (copy / "b.yaml").write_text(
        "topic:\n  key: b\n  title: B\nevents:\n  - key: b1\n    t0: 2026-10-14T10:00:00+02:00\n"
        "    phase: Analysis\n    actor: X\n    action: Schritt B\n    status: open\n",
        encoding="utf-8",
    )
    import_folder(tmp_path / "chronicle.db", copy)
    return copy


@pytest.fixture
def conn(folder):
    connection = connect(folder.parent / "chronicle.db")
    yield connection
    connection.close()


def refs_of(path: Path) -> list[str]:
    return yaml.safe_load(path.read_text(encoding="utf-8")).get("refs", [])


def link(conn, folder, key, topics):
    current = repository.get_event(conn, key)
    data = {field: current[field] for field in ("key", "t0", "phase", "actor", "system", "action", "status",
                                                "triggered_by", "details")}
    return repository.update_event(conn, key, Event.model_validate(data), topics, current["version"],
                                   actor_id=None, ip="test", data_dir=folder)


def test_adding_a_link_writes_refs_into_the_other_topic_file(folder, conn):
    main_file = (folder / "timeline.yaml").read_bytes()

    updated = link(conn, folder, "analysis", ["inc-2026-042", "b"])

    assert refs_of(folder / "b.yaml") == ["analysis"]
    assert (folder / "timeline.yaml").read_bytes() == main_file  # the home file is untouched
    assert sorted(updated["topics"]) == ["b", "inc-2026-042"]


def test_removing_a_link_removes_the_refs_line(folder, conn):
    link(conn, folder, "analysis", ["inc-2026-042", "b"])
    updated = link(conn, folder, "analysis", ["inc-2026-042"])

    assert "refs" not in yaml.safe_load((folder / "b.yaml").read_text(encoding="utf-8"))
    assert updated["topics"] == ["inc-2026-042"]


def test_a_link_and_a_field_change_in_one_save(folder, conn):
    current = repository.get_event(conn, "analysis")
    data = {field: current[field] for field in ("key", "t0", "phase", "actor", "system", "action", "status",
                                                "triggered_by", "details")}
    data["action"] = "zusammen geändert"
    repository.update_event(conn, "analysis", Event.model_validate(data), ["inc-2026-042", "b"], current["version"],
                            actor_id=None, ip="test", data_dir=folder)

    assert "zusammen geändert" in (folder / "timeline.yaml").read_text(encoding="utf-8")
    assert refs_of(folder / "b.yaml") == ["analysis"]


def test_a_new_event_with_two_topics_gets_its_link_in_the_other_file(folder, conn):
    event = Event.model_validate({"key": "neu-2", "t0": "2026-10-27T10:00:00+02:00", "phase": "Activity",
                                  "actor": "Analyst", "action": "Verknüpft", "status": "open"})
    created = repository.create_event(conn, event, ["inc-2026-042", "b"], actor_id=None, ip="test", data_dir=folder)

    assert "key: neu-2" in (folder / "timeline.yaml").read_text(encoding="utf-8")
    assert refs_of(folder / "b.yaml") == ["neu-2"]
    assert sorted(created["topics"]) == ["b", "inc-2026-042"]


def test_deleting_a_linked_event_removes_the_link_first(folder, conn):
    link(conn, folder, "lessons", ["inc-2026-042", "b"])  # "lessons" triggers nothing, so it may be deleted

    repository.delete_event(conn, "lessons", actor_id=None, ip="test", data_dir=folder)

    assert "refs" not in yaml.safe_load((folder / "b.yaml").read_text(encoding="utf-8"))
    assert "key: lessons" not in (folder / "timeline.yaml").read_text(encoding="utf-8")
    assert repository.get_event(conn, "lessons") is None


def test_a_link_to_an_unknown_topic_is_refused(folder, conn):
    with pytest.raises(NotFound, match="gibt-es-nicht"):
        link(conn, folder, "analysis", ["inc-2026-042", "gibt-es-nicht"])
    assert "refs" not in yaml.safe_load((folder / "timeline.yaml").read_text(encoding="utf-8"))
