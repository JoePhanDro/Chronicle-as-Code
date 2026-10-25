"""Hardening of what the data folder can make the app do (links, code in the folder). Part 0a of the vault plan."""

import os

import pytest

from chronicle.check import check_folder
from chronicle.loader import TimelineError, find_topic_files
from chronicle.models import Timeline
from chronicle.store import LOCK_FILE, StoreError, _atomic_write, folder_lock, write_timeline

TOPIC = ("topic:\n  key: a\n  title: A\nevents:\n  - key: a1\n    t0: 2026-10-14T08:40:00+02:00\n"
         "    phase: Exploit\n    actor: X\n    action: Schritt\n    status: open\n")
SECRET = "GEHEIMER-INHALT"


@pytest.fixture
def outside(tmp_path):
    """A file outside the data folder that a link in the folder points to."""
    path = tmp_path / "outside" / "target.txt"
    path.parent.mkdir()
    path.write_text(SECRET, encoding="utf-8")
    return path


@pytest.fixture
def folder(tmp_path):
    path = tmp_path / "data"
    path.mkdir()
    (path / "a.yaml").write_text(TOPIC, encoding="utf-8")
    return path


# --- writing does not follow links -------------------------------------------------------------------------------

def test_atomic_write_does_not_follow_the_old_temporary_name(folder, outside):
    (folder / ".a.yaml.tmp").symlink_to(outside)

    _atomic_write(folder / "a.yaml", b"neu")

    assert outside.read_text(encoding="utf-8") == SECRET
    assert (folder / "a.yaml").read_bytes() == b"neu"


def test_atomic_write_keeps_the_mode_of_the_file_and_leaves_no_temporary_file(folder):
    path = folder / "a.yaml"
    path.chmod(0o640)

    _atomic_write(path, b"neu")

    assert path.stat().st_mode & 0o777 == 0o640
    assert [item.name for item in folder.iterdir()] == ["a.yaml"]


def test_atomic_write_refuses_a_linked_target(folder, outside):
    (folder / "b.yaml").symlink_to(outside)

    with pytest.raises(StoreError, match="b.yaml"):
        _atomic_write(folder / "b.yaml", b"neu")

    assert outside.read_text(encoding="utf-8") == SECRET
    assert (folder / "b.yaml").is_symlink()


def test_atomic_write_leaves_the_file_alone_when_writing_fails(folder, monkeypatch):
    def broken(*_):
        raise OSError("voll")

    monkeypatch.setattr(os, "fsync", broken)
    with pytest.raises(OSError):
        _atomic_write(folder / "a.yaml", b"neu")

    assert (folder / "a.yaml").read_text(encoding="utf-8") == TOPIC
    assert [item.name for item in folder.iterdir()] == ["a.yaml"]


def test_a_linked_lock_file_is_an_error_and_the_target_stays_untouched(folder, tmp_path):
    target = tmp_path / "outside-lock"
    (folder / LOCK_FILE).symlink_to(target)

    with pytest.raises(StoreError, match=LOCK_FILE):
        with folder_lock(folder):
            pytest.fail("the lock must not be taken")

    assert not target.exists()


def test_write_timeline_through_a_linked_temporary_name_does_not_touch_the_target(folder, outside):
    (folder / ".new.yaml.tmp").symlink_to(outside)
    timeline = Timeline.model_validate({"topic": {"key": "n", "title": "N"}})

    write_timeline(folder / "new.yaml", timeline, expected_version=None)

    assert outside.read_text(encoding="utf-8") == SECRET


# --- reading does not follow links -------------------------------------------------------------------------------

def test_a_linked_topic_file_is_refused_by_name_without_its_content(folder, outside):
    outside.write_text(TOPIC.replace("key: a\n", "key: z\n").replace("title: A", f"title: {SECRET}"), encoding="utf-8")
    (folder / "x.yaml").symlink_to(outside)

    with pytest.raises(TimelineError) as caught:
        find_topic_files(folder)
    problems = check_folder(folder, evidence=False)

    assert "x.yaml" in str(caught.value)
    assert problems and "x.yaml" in problems[0]
    assert SECRET not in str(caught.value) + "".join(problems)


def test_evidence_in_a_linked_folder_is_refused(folder):
    (folder / "real").mkdir()
    (folder / "real" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    (folder / "evidence").symlink_to(folder / "real", target_is_directory=True)
    text = TOPIC + "    evidence:\n      - file: evidence/a.png\n        type: screenshot\n"
    (folder / "a.yaml").write_text(text, encoding="utf-8")

    problems = check_folder(folder)

    assert problems and "evidence/a.png" in problems[0]


def test_linked_evidence_is_refused_even_inside_the_folder(folder):
    (folder / "evidence").mkdir()
    (folder / "evidence" / "real.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    (folder / "evidence" / "link.png").symlink_to(folder / "evidence" / "real.png")
    text = TOPIC + "    evidence:\n      - file: evidence/link.png\n        type: screenshot\n"
    (folder / "a.yaml").write_text(text, encoding="utf-8")

    problems = check_folder(folder)

    assert problems and "evidence/link.png" in problems[0]


# --- the root of the folder holds no code ------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["yaml.py", "sitecustomize.py", "usercustomize.py", "pydantic.pyc", "x.so", "chronicle"])
def test_importable_code_in_the_root_is_a_check_failure(folder, name):
    (folder / name).write_text("", encoding="utf-8")

    problems = check_folder(folder, evidence=False)

    assert problems and name in problems[0]


def test_a_package_in_the_root_is_a_check_failure(folder):
    (folder / "yaml").mkdir()
    (folder / "yaml" / "__init__.py").write_text("", encoding="utf-8")

    problems = check_folder(folder, evidence=False)

    assert problems and "yaml" in problems[0]


def test_python_files_below_the_root_and_other_files_are_fine(folder):
    (folder / "scripts").mkdir()
    (folder / "scripts" / "tool.py").write_text("", encoding="utf-8")
    (folder / "README.md").write_text("x", encoding="utf-8")

    assert check_folder(folder, evidence=False) == []
