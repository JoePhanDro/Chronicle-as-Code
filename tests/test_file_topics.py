"""File mode (ADR 0008, issue #89): creating and editing topics writes the topic file, then the index follows it."""

from pathlib import Path

import pytest
import yaml

from chronicle import repository
from chronicle.models import Event, Topic
from chronicle.repository import Conflict, Invalid


def new_topic(key="neu-2026", **changes):
    data = {"key": key, "title": "Neues Topic", "kind": "project", "summary": ""}
    data.update(changes)
    return Topic.model_validate(data)


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_editing_a_topic_changes_its_block_and_keeps_comments_and_events(folder, conn):
    main_file = folder / "timeline.yaml"
    events_before = load(main_file)["events"]

    updated = repository.update_topic(conn, "inc-2026-042", new_topic("inc-2026-042", title="Neuer Titel",
                                                                       kind="incident", summary="Kurz"),
                                      actor_id=None, ip="test", data_dir=folder)

    text = main_file.read_text(encoding="utf-8")
    assert text.startswith("# yaml-language-server")  # the header comment stays
    assert "# Beispiel-Timeline." in text
    document = load(main_file)
    assert document["topic"] == {"key": "inc-2026-042", "title": "Neuer Titel", "kind": "incident", "summary": "Kurz"}
    assert document["events"] == events_before
    assert updated["title"] == "Neuer Titel"
    assert repository.get_topic(conn, "inc-2026-042")["summary"] == "Kurz"


def test_clearing_the_summary_removes_it_from_the_file(folder, conn):
    repository.update_topic(conn, "inc-2026-042", new_topic("inc-2026-042", title="T", kind="incident", summary=""),
                            actor_id=None, ip="test", data_dir=folder)

    assert "summary" not in load(folder / "timeline.yaml")["topic"]


def test_a_changed_topic_file_is_not_overwritten(folder, conn):
    main_file = folder / "timeline.yaml"
    main_file.write_text(main_file.read_text(encoding="utf-8") + "\n# von Hand geändert\n", encoding="utf-8")
    before = main_file.read_bytes()

    with pytest.raises(Conflict):
        repository.update_topic(conn, "inc-2026-042", new_topic("inc-2026-042", title="Anders"),
                                actor_id=None, ip="test", data_dir=folder)
    assert main_file.read_bytes() == before
    assert repository.get_topic(conn, "inc-2026-042")["title"] != "Anders"


def test_a_new_topic_gets_its_own_file_without_events(folder, conn):
    created = repository.create_topic(conn, new_topic(summary="Zweizeilig"), actor_id=None, ip="test", data_dir=folder)

    document = load(folder / "neu-2026.yaml")
    assert document == {"topic": {"key": "neu-2026", "title": "Neues Topic", "kind": "project", "summary": "Zweizeilig"}}
    assert created["key"] == "neu-2026"
    assert repository.get_topic(conn, "neu-2026")["kind"] == "project"


def test_a_topic_key_that_already_exists_is_refused_before_a_file_is_written(folder, conn):
    with pytest.raises(Conflict):
        repository.create_topic(conn, new_topic("inc-2026-042"), actor_id=None, ip="test", data_dir=folder)
    assert not (folder / "inc-2026-042.yaml").exists()


def test_an_existing_file_of_that_name_is_never_overwritten(folder, conn):
    stray = folder / "neu-2026.yaml"
    stray.write_text("topic: {key: anderes, title: Fremd, kind: other}\n", encoding="utf-8")
    before = stray.read_bytes()

    with pytest.raises(Conflict):
        repository.create_topic(conn, new_topic(), actor_id=None, ip="test", data_dir=folder)
    assert stray.read_bytes() == before


def event(key, **changes):
    data = {"key": key, "t0": "2026-10-27T10:00:00+02:00", "phase": "Activity", "actor": "Analyst",
            "action": f"Schritt {key}", "status": "open"}
    data.update(changes)
    return Event.model_validate(data)


def make_topic_with_events(folder, conn):
    """Topic 'weg' with w1 and w2 (w2 triggered by w1); w1 is also linked into the example topic."""
    repository.create_topic(conn, new_topic("weg"), actor_id=None, ip="test", data_dir=folder)
    repository.create_event(conn, event("w1"), ["weg"], actor_id=None, ip="test", data_dir=folder)
    repository.create_event(conn, event("w2", triggered_by=["w1"]), ["weg"], actor_id=None, ip="test", data_dir=folder)
    repository.link_event(conn, "inc-2026-042", "w1", actor_id=None, ip="test", data_dir=folder)


def test_deleting_a_topic_removes_its_file_its_events_and_the_links_to_them(folder, conn):
    make_topic_with_events(folder, conn)
    main_before = load(folder / "timeline.yaml")["events"]

    repository.delete_topic(conn, "weg", actor_id=None, ip="test", data_dir=folder)

    assert not (folder / "weg.yaml").exists()
    assert repository.get_topic(conn, "weg") is None
    assert repository.get_event(conn, "w1") is None and repository.get_event(conn, "w2") is None
    assert "w1" not in (folder / "timeline.yaml").read_text(encoding="utf-8")  # the ref in the other file is gone
    assert load(folder / "timeline.yaml")["events"] == main_before  # and nothing else in it changed
    repository.reindex(conn, folder)  # the folder and the index agree
    assert repository.get_topic(conn, "weg") is None and repository.get_event(conn, "w1") is None


def test_events_of_another_topic_that_depend_on_the_topic_block_its_deletion(folder, conn):
    make_topic_with_events(folder, conn)
    repository.create_event(conn, event("fremd", triggered_by=["w2"]), ["inc-2026-042"], actor_id=None, ip="test",
                            data_dir=folder)
    files_before = {path: path.read_bytes() for path in folder.glob("*.yaml")}

    with pytest.raises(Conflict, match="fremd"):
        repository.delete_topic(conn, "weg", actor_id=None, ip="test", data_dir=folder)

    assert {path: path.read_bytes() for path in folder.glob("*.yaml")} == files_before
    assert repository.get_event(conn, "w2") is not None


def test_a_topic_file_changed_outside_is_not_deleted(folder, conn):
    make_topic_with_events(folder, conn)
    with (folder / "weg.yaml").open("a", encoding="utf-8") as handle:
        handle.write("# von Hand ergänzt\n")

    with pytest.raises(Conflict):
        repository.delete_topic(conn, "weg", actor_id=None, ip="test", data_dir=folder)
    assert (folder / "weg.yaml").exists() and repository.get_topic(conn, "weg") is not None


def test_a_topic_file_is_removed_again_when_the_folder_no_longer_checks(folder, conn):
    (folder / "kaputt.yaml").write_text("topic: [kaputt\n", encoding="utf-8")  # broken file, added outside Chronicle

    with pytest.raises(Invalid):
        repository.create_topic(conn, new_topic("neu"), actor_id=None, ip="test", data_dir=folder)
    assert not (folder / "neu.yaml").exists()
