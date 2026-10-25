"""Abgleichen (ADR 0008, Punkt 3; issue #90): what would change in the Git remote, before anything is committed.

The data folder is a Git repository of its own. The base of the preview is its last commit, the current state is the
working tree after the checks of check.py. The preview lists the events that would be added, changed, moved or removed,
like `terraform plan`. Attachments are not in Git (ADR 0009) and are not compared here.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .check import load_named_folder
from .loader import CheckFailed, TimelineError, parse_timeline
from .models import Timeline
from .store import folder_lock

LINK_MODES = ("120000", "160000")  # symbolic link and submodule in a Git tree
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


def _run(data_dir: Path, *args: str) -> subprocess.CompletedProcess:
    """Run git in the data folder. The exit code is the caller's to read."""
    try:
        return subprocess.run(["git", "-C", str(data_dir), *args], capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise SyncError("Git ist auf diesem Rechner nicht installiert.") from exc


def _git(data_dir: Path, *args: str) -> str:
    result = _run(data_dir, *args)
    if result.returncode != 0:
        raise SyncError(f"Git meldet einen Fehler: {result.stderr.strip() or result.returncode}")
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
    linked = _linked_entries(data_dir, ref)
    if linked:
        raise SyncError(f"Der Stand {ref} enthält Links oder Submodule, die nicht erlaubt sind: {', '.join(linked)}")
    names = [name for name in _git(data_dir, "ls-tree", "-r", "--name-only", ref).splitlines()
             if name.endswith(TOPIC_SUFFIXES)]
    timelines = {}
    for name in names:
        try:
            timelines[name] = parse_timeline(_git(data_dir, "show", f"{ref}:{name}"), f"{ref}: {name}")
        except TimelineError as exc:
            raise SyncError(f"Der Stand {ref} enthält eine Datei, die nicht lesbar ist: {exc}") from exc
    return timelines


def _linked_entries(data_dir: Path, ref: str) -> list[str]:
    """The paths in the tree of ref that are a symbolic link (mode 120000) or a submodule (160000). Only the names."""
    entries = _git(data_dir, "ls-tree", "-r", "-z", ref).split("\0")
    # Each entry is "<mode> <type> <sha>\t<path>"; the path may hold any character except NUL.
    return sorted(path for meta, _, path in (entry.partition("\t") for entry in entries if entry)
                  if meta.split(" ", 1)[0] in LINK_MODES)


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
    except TimelineError as exc:  # e.g. a linked topic file
        return Preview(changes=[], problems=[str(exc)])
    return Preview(changes=plan(base_timelines(data_dir), current), problems=[])


REMOTE = "origin"
PACKAGE_ROOT = Path(__file__).resolve().parent.parent  # the folder that holds the chronicle package
MERGE_DRIVER = "chronicle"  # the name in .gitattributes and in the git config, see chronicle/merge.py


@dataclass(frozen=True)
class SyncResult:
    committed: bool  # the local topic files were committed (nothing was committed if they had no changes)
    merged: bool  # the remote's state was taken in and changed the files
    conflicts: list[str]  # the merge conflicted; it was aborted, nothing was pushed
    problems: list[str]  # the merged files fail the checks; the merge was undone, nothing was pushed


def synchronise(data_dir: Path) -> SyncResult:
    """Commit the local topic files, take in the remote's state and push (ADR 0008, point 3).

    Stops before each step that could lose data: a file that fails the checks, a conflict, a merge that breaks the
    files. A failed upload leaves the local commit in place; the next synchronisation tries again.
    """
    state = preview(data_dir)
    if state.problems:
        return SyncResult(committed=False, merged=False, conflicts=[], problems=state.problems)
    with folder_lock(data_dir):
        _use_merge_driver(data_dir)
        committed = _commit_topic_files(data_dir)
        branch = _git(data_dir, "rev-parse", "--abbrev-ref", "HEAD").strip()
        if branch == "HEAD":
            raise SyncError("Die Datenkopie ist auf keinem Branch. Bitte zuerst einen Branch auschecken.")
        _fetch(data_dir)

        remote_ref = f"{REMOTE}/{branch}"
        if not _ref_exists(data_dir, remote_ref):
            _push(data_dir, branch)  # the first upload: nothing to merge yet
            return SyncResult(committed=committed, merged=False, conflicts=[], problems=[])

        before = _git(data_dir, "rev-parse", "HEAD").strip()
        outcome = _run(data_dir, "merge", "--no-edit", remote_ref)
        if outcome.returncode != 0:
            conflicts = _git(data_dir, "diff", "--name-only", "--diff-filter=U").splitlines()
            if not conflicts:  # refused before it started, e.g. an untracked file is in the way: nothing to abort
                raise SyncError(f"Das Zusammenführen ist fehlgeschlagen: {outcome.stderr.strip() or outcome.stdout.strip()}")
            _git(data_dir, "merge", "--abort")
            return SyncResult(committed=committed, merged=False, conflicts=conflicts, problems=[])

        merged = _git(data_dir, "rev-parse", "HEAD").strip() != before
        linked = _linked_entries(data_dir, "HEAD")
        if linked:  # a link in the merged state would make the app read or write outside the folder
            _git(data_dir, "reset", "--hard", before)
            problems = [f"{name}: Links und Submodule sind im Datenordner nicht erlaubt" for name in linked]
            return SyncResult(committed=committed, merged=False, conflicts=[], problems=problems)
        try:
            load_named_folder(data_dir, evidence=False)
        except CheckFailed as exc:
            _git(data_dir, "reset", "--hard", before)
            return SyncResult(committed=committed, merged=False, conflicts=[], problems=list(exc.problems))
        _push(data_dir, branch)
        return SyncResult(committed=committed, merged=merged, conflicts=[], problems=[])


def _driver_command() -> str:
    """The merge driver as git runs it: in isolated mode, so that the data folder, which is its working directory,
    and the environment add nothing to the module search path. A yaml.py in the folder would run otherwise.

    -I also drops PYTHONPATH, so the program adds the folder of the chronicle package itself.
    """
    program = ("import sys; sys.path.insert(0, sys.argv[1]); from chronicle.merge import main; "
               "raise SystemExit(main(sys.argv[2:]))")
    return f"{shlex.quote(sys.executable)} -I -c {shlex.quote(program)} {shlex.quote(str(PACKAGE_ROOT))} %O %A %B"


def _use_merge_driver(data_dir: Path) -> None:
    """Make the data folder use the merge driver of chronicle/merge.py. The setting is per clone, so it is set here.

    The attribute goes to .git/info/attributes, which is local and not committed. Existing lines are kept.
    """
    _git(data_dir, "config", "--local", f"merge.{MERGE_DRIVER}.name", "Chronicle: Feldebene")
    _git(data_dir, "config", "--local", f"merge.{MERGE_DRIVER}.driver",
         _driver_command())
    attributes = data_dir / _git(data_dir, "rev-parse", "--git-path", "info/attributes").strip()
    existing = attributes.read_text(encoding="utf-8") if attributes.exists() else ""
    wanted = [f"*{suffix} merge={MERGE_DRIVER}" for suffix in TOPIC_SUFFIXES]
    missing = [line for line in wanted if line not in existing.splitlines()]
    if missing:
        attributes.parent.mkdir(parents=True, exist_ok=True)
        attributes.write_text(existing + "".join(f"{line}\n" for line in missing), encoding="utf-8")


def _commit_topic_files(data_dir: Path) -> bool:
    """Commit the topic files only. Attachments and the lock file are not in Git. Returns False if nothing changed."""
    # Named one by one: a pathspec that matches nothing makes git fail, and a folder may have no .yml files.
    changed = _git(data_dir, "ls-files", "-m", "-d", "-o", "--exclude-standard").splitlines()
    topic_files = [name for name in changed if name.endswith(TOPIC_SUFFIXES)]
    if not topic_files:
        return False
    _git(data_dir, "add", "-A", "--", *topic_files)
    if _run(data_dir, "diff", "--cached", "--quiet").returncode == 0:
        return False
    _git(data_dir, "commit", "-q", "-m", f"Abgleich {datetime.now(UTC):%Y-%m-%d %H:%M} UTC")
    return True


def _fetch(data_dir: Path) -> None:
    result = _run(data_dir, "fetch", "--quiet", REMOTE)
    if result.returncode != 0:
        raise SyncError("Der Remote ist nicht erreichbar. Lokal bleibt alles erhalten, der Abgleich lässt sich später "
                        f"wiederholen.\n{result.stderr.strip()}")


def _ref_exists(data_dir: Path, ref: str) -> bool:
    return _run(data_dir, "rev-parse", "--verify", "--quiet", ref).returncode == 0


def _push(data_dir: Path, branch: str) -> None:
    result = _run(data_dir, "push", "--quiet", "-u", REMOTE, f"HEAD:refs/heads/{branch}")
    if result.returncode != 0:
        raise SyncError("Hochladen ist fehlgeschlagen. Die Änderungen sind lokal committet und bleiben erhalten.\n"
                        f"{result.stderr.strip()}")
