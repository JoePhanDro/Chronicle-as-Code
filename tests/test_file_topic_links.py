"""File mode (ADR 0008): linking an event to another topic on the topic page writes the topic file.

Before, the link lived only in the index, and the next rebuild removed it again.
"""

from pathlib import Path

import pytest
import yaml

from chronicle import repository
from chronicle.repository import Invalid
from chronicle.store import LOCK_FILE


@pytest.fixture
def folder(folder_with_b):
    return folder_with_b


def refs_of(folder: Path, topic_file: str) -> list[str]:
    return yaml.safe_load((folder / topic_file).read_text(encoding="utf-8")).get("refs", [])


def linked_keys(conn, topic_key: str) -> list[str]:
    return [event["key"] for event in repository.list_topic_events(conn, topic_key)]


def snapshot(folder: Path) -> dict[str, bytes]:
    return {str(path.relative_to(folder)): path.read_bytes()
            for path in folder.rglob("*") if path.is_file() and path.name != LOCK_FILE}


def test_a_link_is_written_into_the_topic_file_and_survives_a_rebuild(folder, conn):
    repository.link_event(conn, "b", "lessons", actor_id=None, ip="test", data_dir=folder)

    assert refs_of(folder, "b.yaml") == ["lessons"]
    repository.reindex(conn, folder)
    assert "lessons" in linked_keys(conn, "b")


def test_an_unlink_removes_the_line_from_the_topic_file(folder, conn):
    repository.link_event(conn, "b", "lessons", actor_id=None, ip="test", data_dir=folder)

    repository.unlink_event(conn, "b", "lessons", actor_id=None, ip="test", data_dir=folder)

    assert refs_of(folder, "b.yaml") == []
    repository.reindex(conn, folder)
    assert "lessons" not in linked_keys(conn, "b")


def test_the_home_topic_of_an_event_cannot_be_linked_again(folder, conn):
    before = snapshot(folder)

    with pytest.raises(Invalid):
        repository.link_event(conn, "inc-2026-042", "lessons", actor_id=None, ip="test", data_dir=folder)

    assert snapshot(folder) == before


def test_a_link_with_a_broken_folder_changes_nothing(folder, conn):
    (folder / "kaputt.yaml").write_text("topic: [kaputt\n", encoding="utf-8")
    before = snapshot(folder)

    with pytest.raises(Invalid):
        repository.link_event(conn, "b", "lessons", actor_id=None, ip="test", data_dir=folder)

    assert snapshot(folder) == before
    assert "lessons" not in linked_keys(conn, "b")
