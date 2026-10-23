"""Git merge driver for topic files (issue #94): per event and per field, against the common ancestor."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from chronicle.merge import MergeConflict, merge_texts
from chronicle.models import Event, Timeline, Topic
from chronicle.store import dump_timeline, _read_model

ROOT = Path(__file__).resolve().parent.parent


def event(key="e1", **changes):
    data = {"key": key, "t0": "2026-10-14T08:00:00+02:00", "phase": "Analysis", "actor": "SIEM",
            "action": "Erster Schritt", "status": "open"}
    data.update(changes)
    return Event.model_validate(data)


def topic(**changes):
    data = {"key": "inc-1", "title": "Vorfall", "kind": "incident"}
    data.update(changes)
    return Topic.model_validate(data)


def text(events=(), refs=(), **topic_changes):
    return dump_timeline(Timeline(topic=topic(**topic_changes), events=list(events), refs=list(refs)))


def events_of(merged_text):
    return {item.key: item for item in _read_model(merged_text).events}


BASE = text(events=[event("e1"), event("e2", action="Zweiter Schritt")])


def test_changes_to_different_fields_of_one_event_merge_cleanly():
    ours = text(events=[event("e1", details="Neue Notiz"), event("e2", action="Zweiter Schritt")])
    theirs = text(events=[event("e1", status="resolved"), event("e2", action="Zweiter Schritt")])

    merged = events_of(merge_texts(BASE, ours, theirs))

    assert merged["e1"].details == "Neue Notiz"
    assert merged["e1"].status.value == "resolved"


def test_the_same_field_changed_on_both_sides_is_a_conflict():
    ours = text(events=[event("e1", action="Ich"), event("e2", action="Zweiter Schritt")])
    theirs = text(events=[event("e1", action="Du"), event("e2", action="Zweiter Schritt")])

    with pytest.raises(MergeConflict, match="e1.action"):
        merge_texts(BASE, ours, theirs)


def test_the_same_change_on_both_sides_is_no_conflict():
    both = text(events=[event("e1", action="Gleich"), event("e2", action="Zweiter Schritt")])

    assert events_of(merge_texts(BASE, both, both))["e1"].action == "Gleich"


def test_a_deletion_on_one_side_and_a_change_on_the_other_is_a_conflict():
    ours = text(events=[event("e2", action="Zweiter Schritt")])  # e1 deleted
    theirs = text(events=[event("e1", status="resolved"), event("e2", action="Zweiter Schritt")])

    with pytest.raises(MergeConflict, match="e1"):
        merge_texts(BASE, ours, theirs)


def test_a_deletion_on_one_side_wins_when_the_other_side_did_not_change_the_event():
    ours = text(events=[event("e2", action="Zweiter Schritt")])  # e1 deleted
    theirs = BASE

    assert set(events_of(merge_texts(BASE, ours, theirs))) == {"e2"}


def test_events_added_on_both_sides_keep_ours_first():
    ours = text(events=[event("e1"), event("e2", action="Zweiter Schritt"), event("unser")])
    theirs = text(events=[event("e1"), event("e2", action="Zweiter Schritt"), event("ihrer")])

    merged = _read_model(merge_texts(BASE, ours, theirs))

    assert [item.key for item in merged.events] == ["e1", "e2", "unser", "ihrer"]


def test_a_new_event_with_the_same_key_on_both_sides_is_a_conflict_unless_identical():
    ours = text(events=[event("e1"), event("e2", action="Zweiter Schritt"), event("neu", action="A")])
    theirs = text(events=[event("e1"), event("e2", action="Zweiter Schritt"), event("neu", action="B")])

    with pytest.raises(MergeConflict, match="neu.action"):
        merge_texts(BASE, ours, theirs)


def test_links_added_or_removed_on_either_side_merge():
    base = text(events=[event("e1")], refs=["x", "y"])
    ours = text(events=[event("e1")], refs=["x", "z"])  # y removed, z added
    theirs = text(events=[event("e1")], refs=["x", "y", "w"])  # w added

    assert _read_model(merge_texts(base, ours, theirs)).refs == ["x", "z", "w"]


def test_a_topic_field_changed_differently_on_both_sides_is_a_conflict():
    ours = text(events=[event("e1")], title="Unser Titel")
    theirs = text(events=[event("e1")], title="Ihr Titel")

    with pytest.raises(MergeConflict, match="topic.title"):
        merge_texts(text(events=[event("e1")]), ours, theirs)


def test_our_comments_survive_the_merge():
    ours = "# Notiz von uns\n" + text(events=[event("e1", details="Neu"), event("e2", action="Zweiter Schritt")])
    theirs = text(events=[event("e1"), event("e2", action="Zweiter Schritt")], summary="Ihr Text")

    merged = merge_texts(BASE, ours, theirs)

    assert merged.startswith("# Notiz von uns\n")
    assert "# yaml-language-server" in merged
    assert _read_model(merged).topic.summary == "Ihr Text"


def test_a_file_merged_with_itself_stays_byte_identical():
    demo = (ROOT / "data" / "timeline.yaml").read_text(encoding="utf-8")

    assert merge_texts(demo, demo, demo) == demo


def test_a_base_that_does_not_exist_yet_merges_two_new_files():
    ours = text(events=[event("e1")])
    theirs = text(events=[event("e1"), event("e2", action="Zweiter Schritt")])

    assert set(events_of(merge_texts("", ours, theirs))) == {"e1", "e2"}


# The real git: a merge in a temporary repository, with the driver configured as in the module docstring.

@pytest.fixture
def repo(tmp_path, monkeypatch):
    if shutil.which("git") is None:
        pytest.skip("git ist nicht installiert")
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@example.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@example.invalid")

    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, text=True)

    git("init", "-q", "-b", "main")
    (tmp_path / ".gitattributes").write_text("timeline.yaml merge=chronicle\n", encoding="utf-8")
    git("config", "merge.chronicle.name", "Chronicle: Feldebene")
    git("config", "merge.chronicle.driver", f"{sys.executable} -m chronicle.merge %O %A %B")
    (tmp_path / "timeline.yaml").write_text(BASE, encoding="utf-8")
    git("add", ".")
    git("commit", "-q", "-m", "Basis")
    git("checkout", "-q", "-b", "andere")
    return tmp_path, git


def test_git_merges_different_fields_without_a_conflict(repo):
    folder, git = repo
    (folder / "timeline.yaml").write_text(text(events=[event("e1", details="Notiz"), event("e2", action="Zweiter Schritt")]),
                                          encoding="utf-8")
    git("commit", "-q", "-am", "Notiz")
    git("checkout", "-q", "main")
    (folder / "timeline.yaml").write_text(text(events=[event("e1", status="resolved"), event("e2", action="Zweiter Schritt")]),
                                          encoding="utf-8")
    git("commit", "-q", "-am", "Status")

    result = git("merge", "--no-edit", "andere")

    assert result.returncode == 0, result.stdout + result.stderr
    merged = events_of((folder / "timeline.yaml").read_text(encoding="utf-8"))
    assert merged["e1"].details == "Notiz"
    assert merged["e1"].status.value == "resolved"


def test_git_reports_a_conflict_for_the_same_field(repo):
    folder, git = repo
    (folder / "timeline.yaml").write_text(text(events=[event("e1", action="Du"), event("e2", action="Zweiter Schritt")]),
                                          encoding="utf-8")
    git("commit", "-q", "-am", "Du")
    git("checkout", "-q", "main")
    (folder / "timeline.yaml").write_text(text(events=[event("e1", action="Ich"), event("e2", action="Zweiter Schritt")]),
                                          encoding="utf-8")
    git("commit", "-q", "-am", "Ich")

    result = git("merge", "--no-edit", "andere")

    assert result.returncode != 0
    assert "UU timeline.yaml" in git("status", "--porcelain").stdout
