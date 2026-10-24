"""Editing one event in its topic file changes only that event's entry (issue #125).

The rest of the file stays byte for byte as it was, so the diff of a change in the app shows only the edited event.
"""

import difflib
from pathlib import Path

import pytest

from chronicle import store
from chronicle.models import Event
from chronicle.store import EventNotInFile

FILE = """\
topic:
  key: t
  title: T
events:
  - key: erster
    t0: 2026-10-14T10:00:00+02:00
    phase: Analysis
    actor: Anna
    action: Erster Schritt
    status: open
    details: |
      Mehrzeilig,
      mit zwei Zeilen.

  # Kommentar zwischen zwei Einträgen
  - key: zweiter
    t0: 2026-10-14T11:00:00+02:00
    phase: Analysis
    actor: Bernd
    action: 'Zweiter Schritt'
    status: open
  - key: letzter
    t0: 2026-10-14T12:00:00+02:00
    phase: Activity
    actor: Carla
    action: Letzter Schritt
    status: resolved
"""


def write(tmp_path: Path) -> Path:
    path = tmp_path / "t.yaml"
    path.write_text(FILE, encoding="utf-8")
    return path


def changed_lines(before: str, after: str) -> list[str]:
    return [line for line in difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="")
            if line[:1] in "+-" and line[:3] not in ("+++", "---")]


def event(key: str, **changes) -> Event:
    data = {"key": key, "t0": "2026-10-14T11:00:00+02:00", "phase": "Analysis", "actor": "Bernd",
            "action": "Zweiter Schritt", "status": "open"}
    data.update(changes)
    return Event.model_validate(data)


def test_only_the_edited_entry_changes_in_the_file(tmp_path):
    path = write(tmp_path)
    version = store.file_version(path)

    store.update_event_in_file(path, "zweiter", event("zweiter", action="Neu formuliert"), expected_version=version)

    after = path.read_text(encoding="utf-8")
    # The quotes of the value stay as they were written.
    assert changed_lines(FILE, after) == ["-    action: 'Zweiter Schritt'", "+    action: 'Neu formuliert'"]


def test_the_last_entry_of_the_file_can_be_edited(tmp_path):
    path = write(tmp_path)
    version = store.file_version(path)

    store.update_event_in_file(path, "letzter", event("letzter", actor="Dora", phase="Activity",
                                                      t0="2026-10-14T12:00:00+02:00", action="Letzter Schritt",
                                                      status="resolved"),
                               expected_version=version)

    after = path.read_text(encoding="utf-8")
    assert changed_lines(FILE, after) == ["-    actor: Carla", "+    actor: Dora"]


def test_an_event_that_is_not_in_the_file_is_refused(tmp_path):
    path = write(tmp_path)
    version = store.file_version(path)

    with pytest.raises(EventNotInFile):
        store.update_event_in_file(path, "gibt-es-nicht", event("gibt-es-nicht"), expected_version=version)

    assert path.read_text(encoding="utf-8") == FILE


def test_an_empty_file_has_no_event_to_edit(tmp_path):
    path = tmp_path / "leer.yaml"
    path.write_text("", encoding="utf-8")
    version = store.file_version(path)

    with pytest.raises(EventNotInFile):
        store.update_event_in_file(path, "erster", event("erster"), expected_version=version)
