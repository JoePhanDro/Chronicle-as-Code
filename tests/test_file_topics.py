"""File mode (ADR 0008, issue #89): creating and editing topics writes the topic file, then the index follows it."""

from pathlib import Path

import pytest
import yaml

from chronicle import repository
from chronicle.models import Topic
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


def test_a_topic_cannot_be_deleted_in_file_mode_yet(folder, conn):
    repository.create_topic(conn, new_topic("weg"), actor_id=None, ip="test", data_dir=folder)

    with pytest.raises(Invalid):
        repository.delete_topic(conn, "weg", actor_id=None, ip="test", data_dir=folder)
    assert (folder / "weg.yaml").exists()
    assert repository.get_topic(conn, "weg") is not None


def test_a_topic_file_is_removed_again_when_the_folder_no_longer_checks(folder, conn):
    (folder / "kaputt.yaml").write_text("topic: [kaputt\n", encoding="utf-8")  # broken file, added outside Chronicle

    with pytest.raises(Invalid):
        repository.create_topic(conn, new_topic("neu"), actor_id=None, ip="test", data_dir=folder)
    assert not (folder / "neu.yaml").exists()
