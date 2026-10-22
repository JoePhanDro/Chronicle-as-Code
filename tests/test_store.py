"""The file writer (ADR 0008, issue #89): atomic, version-checked, read-back verified."""

from pathlib import Path

import pytest
import yaml

from chronicle.check import check_folder
from chronicle.loader import _YAML_LOADER, find_topic_files, load_timeline
from chronicle.models import Timeline
from chronicle.store import LOCK_FILE, StoreError, VersionConflict, dump_timeline, file_version, write_timeline

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def timeline(*, details="", key="a1"):
    return Timeline.model_validate({
        "topic": {"key": "a", "title": "Titel mit Umlauten: Äöü — ✓"},
        "events": [{
            "key": key, "t0": "2026-10-14T08:40:00+02:00", "phase": "Exploit", "actor": "Angreifer",
            "action": "Ausnutzung", "status": "in_progress", "details": details,
            "evidence": [{"file": "evidence/waf.png", "type": "screenshot", "description": "WAF"}],
        }],
    })


def test_dump_reads_back_as_the_same_timeline():
    original = Timeline.model_validate(yaml.load((DATA_DIR / "timeline.yaml").read_text(encoding="utf-8"),
                                                 Loader=_YAML_LOADER))
    text = dump_timeline(original)
    assert Timeline.model_validate(yaml.load(text, Loader=_YAML_LOADER)) == original


def test_markdown_and_special_characters_survive_the_round_trip():
    details = "**fett**\n\n- Liste\n- mit `code` und Umlauten äöü\n```\nblock\n```"
    text = dump_timeline(timeline(details=details))
    assert Timeline.model_validate(yaml.load(text, Loader=_YAML_LOADER)).events[0].details == details


def test_empty_optional_fields_are_left_out():
    text = dump_timeline(Timeline.model_validate({"topic": {"key": "a", "title": "A"}}))
    assert "events" not in text and "refs" not in text and "summary" not in text
    assert text.startswith("# yaml-language-server: $schema=")


def test_a_new_file_is_written_atomically_and_checked(tmp_path):
    path = tmp_path / "a.yaml"
    version = write_timeline(path, timeline(), expected_version=None)

    assert version == file_version(path)
    assert not (tmp_path / f".{path.name}.tmp").exists()  # the temporary file was renamed
    assert check_folder(tmp_path, evidence=False) == []
    assert (tmp_path / LOCK_FILE).exists()
    assert find_topic_files(tmp_path) == [path]  # the lock file is not a topic file


def test_a_stale_version_is_refused_and_the_file_is_left_alone(tmp_path):
    path = tmp_path / "a.yaml"
    first = write_timeline(path, timeline(), expected_version=None)
    write_timeline(path, timeline(details="zweite Fassung"), expected_version=first)
    current = path.read_bytes()

    with pytest.raises(VersionConflict):
        write_timeline(path, timeline(details="veraltet"), expected_version=first)
    assert path.read_bytes() == current


def test_an_edit_made_outside_the_app_is_noticed(tmp_path):
    path = tmp_path / "a.yaml"
    version = write_timeline(path, timeline(), expected_version=None)
    path.write_text(path.read_text(encoding="utf-8").replace("Ausnutzung", "von Hand geändert"), encoding="utf-8")

    with pytest.raises(VersionConflict):
        write_timeline(path, timeline(details="App"), expected_version=version)
    assert "von Hand geändert" in path.read_text(encoding="utf-8")


def test_creating_over_an_existing_file_is_refused(tmp_path):
    path = tmp_path / "a.yaml"
    write_timeline(path, timeline(), expected_version=None)
    with pytest.raises(VersionConflict, match="existiert bereits"):
        write_timeline(path, timeline(), expected_version=None)


def test_the_written_file_loads_as_the_same_timeline(tmp_path):
    path = tmp_path / "a.yaml"
    original = timeline(details="Text")
    write_timeline(path, original, expected_version=None)
    assert load_timeline(path) == original
