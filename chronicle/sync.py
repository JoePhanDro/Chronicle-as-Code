"""Abgleichen (ADR 0008, Punkt 3; issue #90): what would change in the Git remote, before anything is committed.

The data folder is a Git repository of its own. The base of the preview is its last commit, the current state is the
working tree after the checks of check.py. The preview lists the events that would be added, changed, moved or removed,
like `terraform plan`. Attachments are not in Git (ADR 0009) and are not compared here.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .check import load_named_folder
from .loader import CheckFailed, TimelineError, parse_timeline
from .models import Timeline

TOPIC_SUFFIXES = (".yaml", ".yml")  # the files the loader reads, see loader.find_topic_files


class SyncError(Exception):
    """The data folder cannot be synchronised as it is (not a Git repository, Git failed, ...)."""


@dataclass(frozen=True)
class Change:
    action: str  # "angelegt", "geändert", "verschoben", "entfernt"; for topics: "Topic angelegt", ...
    key: str
    file: str  # the topic file the item is in after the change; for a removal, the file it was in


@dataclass(frozen=True)
class Preview:
    changes: list[Change]
    problems: list[str]  # not empty: the files fail the checks, so no plan can be made


def _git(data_dir: Path, *args: str) -> str:
    try:
        result = subprocess.run(["git", "-C", str(data_dir), *args], capture_output=True, text=True, check=True)
    except FileNotFoundError as exc:
        raise SyncError("Git ist auf diesem Rechner nicht installiert.") from exc
    except subprocess.CalledProcessError as exc:
        raise SyncError(f"Git meldet einen Fehler: {exc.stderr.strip() or exc}") from exc
    return result.stdout


def _is_repository_root(data_dir: Path) -> bool:
    """The data folder must be the root of its Git repository, so that the file names match the commit's."""
    try:
        top = _git(data_dir, "rev-parse", "--show-toplevel").strip()
    except SyncError:
        return False
    return Path(top).resolve() == data_dir.resolve()


def base_ref(data_dir: Path) -> str | None:
    """The state the remote last had: the upstream branch. Without one, the last local commit. None before any commit.

    Committed but unpushed changes count as changes: a synchronisation would send them.
    """
    for ref in ("@{u}", "HEAD"):
        try:
            _git(data_dir, "rev-parse", "--verify", "--quiet", ref)
        except SyncError:
            continue
        return ref
    return None


def base_timelines(data_dir: Path) -> dict[str, Timeline]:
    """The topic files at the base ref. Empty when there is none yet."""
    ref = base_ref(data_dir)
    if ref is None:
        return {}
    names = [name for name in _git(data_dir, "ls-tree", "-r", "--name-only", ref).splitlines()
             if name.endswith(TOPIC_SUFFIXES)]
    timelines = {}
    for name in names:
        try:
            timelines[name] = parse_timeline(_git(data_dir, "show", f"{ref}:{name}"), f"{ref}: {name}")
        except TimelineError as exc:
            raise SyncError(f"Der Stand {ref} enthält eine Datei, die nicht lesbar ist: {exc}") from exc
    return timelines


def plan(base: Mapping[str, Timeline], current: Mapping[str, Timeline]) -> list[Change]:
    """The changes from base to current, by event key and by topic. Sorted, so the same input gives the same list."""
    before = {event.key: (name, event) for name, timeline in base.items() for event in timeline.events}
    after = {event.key: (name, event) for name, timeline in current.items() for event in timeline.events}

    changes: list[Change] = []
    for key in sorted(before.keys() | after.keys()):
        if key not in after:
            changes.append(Change("entfernt", key, before[key][0]))
        elif key not in before:
            changes.append(Change("angelegt", key, after[key][0]))
        else:
            (old_file, old), (new_file, new) = before[key], after[key]
            if old != new:
                changes.append(Change("geändert", key, new_file))
            elif old_file != new_file:
                changes.append(Change("verschoben", key, new_file))

    for name in sorted(base.keys() | current.keys()):
        old_topic = base[name].topic if name in base else None
        new_topic = current[name].topic if name in current else None
        if old_topic is None:
            changes.append(Change("Topic angelegt", new_topic.key, name))
        elif new_topic is None:
            changes.append(Change("Topic entfernt", old_topic.key, name))
        elif old_topic != new_topic:
            changes.append(Change("Topic geändert", new_topic.key, name))
    return changes


def preview(data_dir: Path) -> Preview:
    """What a synchronisation would change. The files must pass the checks first; otherwise the problems are returned."""
    if not _is_repository_root(data_dir):
        raise SyncError("Der Datenordner muss das Wurzelverzeichnis seines Git-Repositorys sein.")
    try:
        current = {name: timeline for name, timeline, _ in load_named_folder(data_dir, evidence=False)}
    except CheckFailed as exc:
        return Preview(changes=[], problems=list(exc.problems))
    return Preview(changes=plan(base_timelines(data_dir), current), problems=[])
