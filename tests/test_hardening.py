"""Hardening of what the data folder can make the app do (links, code in the folder). Part 0a of the vault plan."""

import os

import pytest

from chronicle.check import check_folder
from chronicle.loader import TimelineError, find_topic_files
from chronicle.models import Timeline
from chronicle.store import LOCK_FILE, StoreError, _atomic_write, folder_lock, write_new_file, write_timeline

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

    assert problems and "evidence" in problems[0]


def test_linked_evidence_is_refused_even_inside_the_folder(folder):
    (folder / "evidence").mkdir()
    (folder / "evidence" / "real.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    (folder / "evidence" / "link.png").symlink_to(folder / "evidence" / "real.png")
    text = TOPIC + "    evidence:\n      - file: evidence/link.png\n        type: screenshot\n"
    (folder / "a.yaml").write_text(text, encoding="utf-8")

    problems = check_folder(folder)

    assert problems and "evidence/link.png" in problems[0]


# --- the root of the folder holds no code ------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["yaml.py", "sitecustomize.py", "usercustomize.py", "pydantic.pyc", "x.so",
                                  "_yaml.cpython-312-darwin.so", "_yaml.abi3.so", "mod.pyd"])
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


@pytest.mark.parametrize("name", ["chronicle.yaml", "chronicle.v2.yaml", "sitecustomize.yaml", "sitecustomize.md", "yaml.json"])
def test_files_that_only_share_a_module_name_are_fine(folder, name):
    (folder / name).write_text(TOPIC.replace("key: a\n", "key: b\n").replace("a1", "b1") if name.endswith(".yaml") else "x", encoding="utf-8")

    assert check_folder(folder, evidence=False) == []


def test_a_folder_without_init_module_is_fine_even_when_named_like_the_app(folder):
    (folder / "chronicle").mkdir()
    (folder / "chronicle" / "notes.txt").write_text("x", encoding="utf-8")

    assert check_folder(folder, evidence=False) == []


def test_a_package_with_a_tagged_init_module_is_a_check_failure(folder):
    (folder / "yaml").mkdir()
    (folder / "yaml" / "__init__.cpython-312-darwin.so").write_text("", encoding="utf-8")

    assert check_folder(folder, evidence=False)[0].startswith("yaml:")


# --- links anywhere in the tree, writing through folders -----------------------------------------------------------

def test_a_linked_folder_without_any_yaml_in_it_is_refused(folder, tmp_path):
    (tmp_path / "out").mkdir()
    (folder / "evidence").symlink_to(tmp_path / "out", target_is_directory=True)

    problems = check_folder(folder, evidence=False)

    assert problems and "evidence" in problems[0]


def test_new_files_do_not_land_outside_through_a_linked_folder(folder, tmp_path):
    (tmp_path / "out").mkdir()
    (folder / "evidence").symlink_to(tmp_path / "out", target_is_directory=True)

    with pytest.raises(StoreError, match="evidence"):
        write_new_file(folder / "evidence" / "k1" / "a.png", b"x", root=folder)

    assert list((tmp_path / "out").iterdir()) == []


def test_the_data_folder_itself_may_be_a_link(tmp_path, folder):
    mount = tmp_path / "mount"
    mount.symlink_to(folder, target_is_directory=True)

    write_new_file(mount / "evidence" / "k1" / "a.png", b"x", root=mount)

    assert (folder / "evidence" / "k1" / "a.png").read_bytes() == b"x"
    assert check_folder(mount, evidence=False) == []


def test_a_folder_named_like_a_topic_file_is_not_a_topic_file(folder):
    (folder / "x.yaml").mkdir()
    (folder / "x.yaml" / "inner.txt").write_text("x", encoding="utf-8")

    assert [path.name for path in find_topic_files(folder)] == ["a.yaml"]
    assert check_folder(folder, evidence=False) == []


def test_many_links_are_listed_in_part(folder, tmp_path):
    for number in range(15):
        (folder / f"l{number:02}.txt").symlink_to(tmp_path)

    with pytest.raises(TimelineError, match="und 5 weitere"):
        find_topic_files(folder)


def test_a_lock_file_that_is_a_folder_is_an_error(folder):
    (folder / LOCK_FILE).mkdir()

    with pytest.raises(StoreError, match=LOCK_FILE):
        with folder_lock(folder):
            pytest.fail("the lock must not be taken")


def test_a_lock_file_that_is_a_pipe_is_an_error(folder):
    os.mkfifo(folder / LOCK_FILE)

    with pytest.raises(StoreError, match=LOCK_FILE):
        with folder_lock(folder):
            pytest.fail("the lock must not be taken")
