"""Abgleichen (ADR 0008, Punkt 3; issue #90): what would change in the Git remote, before anything is committed.

The data folder is a Git repository of its own. The base of the preview is its last commit, the current state is the
working tree after the checks of check.py. The preview lists the events that would be added, changed, moved or removed,
like `terraform plan`. Attachments are not in Git (ADR 0009) and are not compared here.
"""

from __future__ import annotations

import difflib
import os
import re
import secrets
import shlex
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import accounts
from .check import load_named_folder
from .loader import TOPIC_SUFFIXES, CheckFailed, TimelineError, decode_topic_bytes, parse_timeline
from .models import Event, Timeline, Topic
from .store import StoreError, folder_lock
from .yamlscan import MAX_YAML_BYTES, TOO_LARGE, one_line

LINK_MODES = ("120000", "160000")  # symbolic link and submodule in a Git tree


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


def _run(data_dir: Path, *args: str, text: bool = True) -> subprocess.CompletedProcess:
    """Run git in the data folder. The exit code is the caller's to read. With text=False the output stays bytes."""
    try:
        return subprocess.run(["git", "-C", str(data_dir), *args], capture_output=True, text=text, check=False)
    except FileNotFoundError as exc:
        raise SyncError("Git ist auf diesem Rechner nicht installiert.") from exc


def _run_network(data_dir: Path, *args: str) -> subprocess.CompletedProcess:
    """Run a git command that talks to the remote. It never asks for a password on the terminal, and it is stopped
    after NETWORK_TIMEOUT_SECONDS, so that a hanging network cannot hold the synchronisation forever."""
    try:
        return subprocess.run(["git", "-C", str(data_dir), *args], capture_output=True, text=True, check=False,
                              env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}, timeout=NETWORK_TIMEOUT_SECONDS)
    except FileNotFoundError as exc:
        raise SyncError("Git ist auf diesem Rechner nicht installiert.") from exc
    except subprocess.TimeoutExpired as exc:
        raise SyncError(f"Der Remote antwortet nicht (nach {NETWORK_TIMEOUT_SECONDS} Sekunden abgebrochen). "
                        "Lokal bleibt alles erhalten, der Abgleich lässt sich später wiederholen.") from exc


def _git(data_dir: Path, *args: str) -> str:
    result = _run(data_dir, *args)
    if result.returncode != 0:
        raise SyncError(f"Git meldet einen Fehler: {result.stderr.strip() or result.returncode}")
    return result.stdout


def _blob(data_dir: Path, commit: str, name: str, where: str) -> bytes:
    """The bytes of a file at a commit. The size is asked first, so that a huge blob is never read into memory."""
    size = _git(data_dir, "cat-file", "-s", f"{commit}:{name}").strip()
    if not size.isdigit() or int(size) > MAX_YAML_BYTES:
        raise TimelineError(f"{one_line(where)}: {TOO_LARGE}")
    result = _run(data_dir, "show", f"{commit}:{name}", text=False)
    if result.returncode != 0:
        raise SyncError(f"Git meldet einen Fehler beim Lesen von {one_line(where)}")
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
    """The topic files at the base ref. Empty when there is none yet.

    Links and submodules in the base are skipped, not refused: only the content of regular files is read from Git,
    nothing is followed. The preview runs before the fetch, so refusing here would block every synchronisation after a
    remote once held a link, even when the remote has removed it since. The link is refused where it would reach
    the folder: in the merge result (see synchronise).
    """
    ref = base_ref(data_dir)
    if ref is None:
        return {}
    commit = _git(data_dir, "rev-parse", "--verify", ref).strip()  # @{u} may move between two calls of git
    names = [path for mode, path in _tree(data_dir, commit) if mode not in LINK_MODES and path.endswith(TOPIC_SUFFIXES)]
    timelines = {}
    for name in names:
        try:
            where = f"{ref}: {name}"
            timelines[name] = parse_timeline(decode_topic_bytes(_blob(data_dir, commit, name, where), where), where)
        except TimelineError as exc:
            raise SyncError(f"Der Stand {ref} enthält eine Datei, die nicht lesbar ist: {exc}") from exc
    return timelines


def _tree(data_dir: Path, ref: str) -> list[tuple[str, str]]:
    """The files of the tree at ref as (mode, path). NUL-separated, so any path is read as it is."""
    entries = _git(data_dir, "ls-tree", "-r", "-z", ref).split("\0")
    # Each entry is "<mode> <type> <sha>\t<path>".
    return [(meta.split(" ", 1)[0], path) for meta, _, path in (entry.partition("\t") for entry in entries if entry)]


def _linked(tree: list[tuple[str, str]]) -> list[str]:
    """The paths that are a symbolic link (mode 120000) or a submodule (160000). Only the names."""
    return sorted(path for mode, path in tree if mode in LINK_MODES)


def _event_change(key: str, before: tuple[str, Event] | None, after: tuple[str, Event] | None) -> Change | None:
    """How one event differs between two states; before and after are (file name, event), or None if it is not there."""
    if after is None:
        return Change("entfernt", key, before[0])
    if before is None:
        return Change("angelegt", key, after[0])
    (old_file, old), (new_file, new) = before, after
    if old != new:
        return Change("geändert", key, new_file)
    if old_file != new_file:
        return Change("verschoben", key, new_file)
    return None


def _event_changes(base: Mapping[str, Timeline], current: Mapping[str, Timeline]) -> list[Change]:
    before = {event.key: (name, event) for name, timeline in base.items() for event in timeline.events}
    after = {event.key: (name, event) for name, timeline in current.items() for event in timeline.events}
    found = (_event_change(key, before.get(key), after.get(key)) for key in sorted(before.keys() | after.keys()))
    return [change for change in found if change is not None]


def _topic_change(name: str, old: Topic | None, new: Topic | None) -> Change | None:
    if old is None:
        return Change("Topic angelegt", new.key, name)
    if new is None:
        return Change("Topic entfernt", old.key, name)
    if old != new:
        return Change("Topic geändert", new.key, name)
    return None


def _topic_changes(base: Mapping[str, Timeline], current: Mapping[str, Timeline]) -> list[Change]:
    found = (
        _topic_change(name, base[name].topic if name in base else None, current[name].topic if name in current else None)
        for name in sorted(base.keys() | current.keys())
    )
    return [change for change in found if change is not None]


def plan(base: Mapping[str, Timeline], current: Mapping[str, Timeline]) -> list[Change]:
    """The changes from base to current, by event key and by topic. Sorted, so the same input gives the same list."""
    return [*_event_changes(base, current), *_topic_changes(base, current)]


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
NETWORK_TIMEOUT_SECONDS = 120
DRIVER_FAILED = "CHRONICLE-TREIBERFEHLER"
MAX_FAILURE_TEXT = 300
PACKAGE_ROOT = Path(__file__).resolve().parent.parent  # the folder that holds the chronicle package
MERGE_DRIVER = "chronicle"  # the name in .gitattributes and in the git config, see chronicle/merge.py


@dataclass(frozen=True)
class ConflictFile:
    """One file the merge could not resolve: its text on both sides (None: the file is not there on that side)."""

    name: str
    ours: str | None  # this copy, with the local changes committed
    theirs: str | None  # the remote's state

    @property
    def diff(self) -> list[str]:
        """The lines that differ, as a unified diff without its header."""
        lines = difflib.unified_diff((self.ours or "").splitlines(), (self.theirs or "").splitlines(),
                                     "dieser Stand", "Remote", lineterm="", n=2)
        return list(lines)[2:]


@dataclass(frozen=True)
class SyncResult:
    committed: bool  # the local topic files were committed (nothing was committed if they had no changes)
    merged: bool  # the remote's state was taken in and changed the files
    conflicts: list[str]  # the merge conflicted; it was aborted, nothing was pushed
    problems: list[str]  # the merged files fail the checks; the merge was undone, nothing was pushed
    sides: list[ConflictFile] = field(default_factory=list)  # both sides of each conflicting file


def synchronise(data_dir: Path) -> SyncResult:
    """Commit the local topic files, take in the remote's state and push (ADR 0008, point 3).

    Stops before each step that could lose data: a file that fails the checks, a conflict, a merge that breaks the
    files. A failed upload leaves the local commit in place; the next synchronisation tries again.

    The folder is locked for the local steps only (commit, merge, checks). Fetching and pushing talk to the network and
    run outside the lock, so that a slow remote does not hold up the changes made in the app (issue #131). The local
    changes are committed before the fetch, so that an unreachable remote still leaves them committed. Changes made
    while the fetch runs are committed again before the merge.
    """
    state = preview(data_dir)
    if state.problems:
        return SyncResult(committed=False, merged=False, conflicts=[], problems=state.problems)
    try:
        branch = _current_branch(data_dir)
        with folder_lock(data_dir):
            marker = _use_merge_driver(data_dir)
            committed = _commit_topic_files(data_dir)
        _fetch(data_dir)
        with folder_lock(data_dir):
            result, upload = _take_in_remote(data_dir, branch, marker, committed)
        if upload:
            _push(data_dir, branch)
        return result
    except StoreError as exc:  # the lock file is a link, a folder, ...
        raise SyncError(str(exc)) from exc


def audit_run(conn, user_id: int, ip: str, result: SyncResult) -> None:
    """Record a run in the audit log, whoever started it (page or API)."""
    accounts.audit(conn, "sync", user_id=user_id, target="remote", ip=ip,
                   detail=f"committed={result.committed},merged={result.merged},conflicts={len(result.conflicts)}")


def _current_branch(data_dir: Path) -> str:
    branch = _git(data_dir, "rev-parse", "--abbrev-ref", "HEAD").strip()
    if branch == "HEAD":
        raise SyncError("Die Datenkopie ist auf keinem Branch. Bitte zuerst einen Branch auschecken.")
    return branch


def _take_in_remote(data_dir: Path, branch: str, marker: str, committed: bool) -> tuple[SyncResult, bool]:
    """The local steps, with the folder locked: commit what changed since, merge the fetched state, check the result.
    `committed` says whether the first commit took changes. The flag returned says whether the state is ready to be
    uploaded."""
    committed = _commit_topic_files(data_dir) or committed

    remote_ref = f"{REMOTE}/{branch}"
    if not _ref_exists(data_dir, remote_ref):
        return SyncResult(committed=committed, merged=False, conflicts=[], problems=[]), True  # the first upload

    before = _git(data_dir, "rev-parse", "HEAD").strip()
    outcome = _run(data_dir, "merge", "--no-edit", remote_ref)
    if outcome.returncode != 0:
        return _failed_merge(data_dir, remote_ref, marker, outcome, committed), False

    merged = _git(data_dir, "rev-parse", "HEAD").strip() != before
    linked = _linked(_tree(data_dir, "HEAD"))
    if linked:  # a link in the merged state would make the app read or write outside the folder
        _undo_merge(data_dir, before)
        problems = [f"{name}: Links und Submodule sind im Datenordner nicht erlaubt" for name in linked]
        return SyncResult(committed=committed, merged=False, conflicts=[], problems=problems), False
    try:
        load_named_folder(data_dir, evidence=False)
    except CheckFailed as exc:
        _undo_merge(data_dir, before)
        return SyncResult(committed=committed, merged=False, conflicts=[], problems=list(exc.problems)), False
    return SyncResult(committed=committed, merged=merged, conflicts=[], problems=[]), True


def _failed_merge(
    data_dir: Path, remote_ref: str, marker: str, outcome: subprocess.CompletedProcess, committed: bool
) -> SyncResult:
    """A merge that did not succeed: a crashed driver and a merge that never started are errors, anything else is a
    conflict. The merge is aborted, and both sides of each conflicting file are read first."""
    failure = _driver_failure(outcome, marker)  # git counts a crashed driver as a conflict
    if failure is not None:
        _run(data_dir, "merge", "--abort")
        raise SyncError(f"Der Merge-Treiber ist abgestürzt, das Zusammenführen wurde abgebrochen: {failure}")
    conflicts = _git(data_dir, "diff", "--name-only", "--diff-filter=U").splitlines()
    if not conflicts:  # refused before it started, e.g. an untracked file is in the way: nothing to abort
        raise SyncError(f"Das Zusammenführen ist fehlgeschlagen: {outcome.stderr.strip() or outcome.stdout.strip()}")
    sides = [_conflict_file(data_dir, remote_ref, name) for name in conflicts]
    _git(data_dir, "merge", "--abort")
    return SyncResult(committed=committed, merged=False, conflicts=conflicts, problems=[], sides=sides)


def _side(data_dir: Path, commit: str, name: str) -> str | None:
    """The text of a file at a commit, or None if the commit has no such file. A file too large to show is replaced
    by a note, and bytes that are not UTF-8 are shown with replacement characters: this is for reading only."""
    if _run(data_dir, "cat-file", "-e", f"{commit}:{name}").returncode != 0:
        return None
    try:
        return _blob(data_dir, commit, name, f"{commit}: {name}").decode("utf-8", errors="replace")
    except TimelineError as exc:
        return str(exc)


def _conflict_file(data_dir: Path, remote_ref: str, name: str) -> ConflictFile:
    return ConflictFile(name, ours=_side(data_dir, "HEAD", name), theirs=_side(data_dir, remote_ref, name))


def _driver_failure(outcome: subprocess.CompletedProcess, marker: str) -> str | None:
    """What a crashed driver reports, or None if the merge failed for another reason (a conflict).

    Only a line that starts with this call's marker counts: a file may carry the marker in its name, which git prints.
    A driver killed by a signal prints no marker, and git then echoes the whole command line: a fixed text is used.
    The reported line is cut and stripped of control characters, because it may hold text from the data folder.
    """
    lines = (outcome.stderr + "\n" + outcome.stdout).splitlines()
    for line in lines:
        if line.startswith(marker):
            return re.sub(r"[\x00-\x1f\x7f]", " ", line[len(marker):]).strip()[:MAX_FAILURE_TEXT]
    if any("died of signal" in line or "failed to execute internal merge" in line for line in lines):
        return "Das Programm wurde unerwartet beendet."
    return None


def _undo_merge(data_dir: Path, before: str) -> None:
    """Back to the commit before the merge. `--merge` keeps uncommitted changes in files the merge did not touch,
    which `--hard` would throw away (issue #126 is the same kind of loss)."""
    _git(data_dir, "reset", "--merge", before)


def _driver_command(data_dir: Path, marker: str) -> str:
    """The merge driver as git runs it. git's working directory for it is the data folder.

    `-P` keeps that directory off the module search path, so a yaml.py in the folder does not run. The environment
    (PYTHONPATH, the user site) stays, because a deployment may keep its dependencies there. The folder of the
    chronicle package is added by the program itself, from the app's own location, never from the data folder.
    A failing driver prints a line that starts with `marker` (unique per call), so that the synchronisation can tell
    it from a conflict. Entries of PYTHONPATH that are empty, relative or inside the data folder are dropped, because
    Python resolves them against the working directory, which is the data folder, and `-P` does not cover that.
    """
    program = (
        "import sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "try:\n"
        "    from chronicle.merge import main\n"
        "    code = main(sys.argv[2:])\n"
        "except Exception as exc:\n"
        f"    print({marker!r}, type(exc).__name__, exc, file=sys.stderr)\n"
        "    code = 70\n"
        "raise SystemExit(code)\n"
    )
    command = f"{shlex.quote(sys.executable)} -P -c {shlex.quote(program)} {shlex.quote(str(PACKAGE_ROOT))}"
    if "PYTHONPATH" in os.environ:
        command = f"env PYTHONPATH={shlex.quote(_clean_python_path(data_dir))} {command}"
    return command.replace("%", "%%") + " %O %A %B"  # git replaces %A, %B, ... in the command line


def _clean_python_path(data_dir: Path) -> str:
    folder = data_dir.resolve()
    kept = [entry for entry in os.environ["PYTHONPATH"].split(os.pathsep)
            if entry and os.path.isabs(entry) and not Path(entry).resolve().is_relative_to(folder)]
    return os.pathsep.join(kept)


def _use_merge_driver(data_dir: Path) -> str:
    """Make the data folder use the merge driver of chronicle/merge.py. The setting is per clone, so it is set here.

    The attribute goes to .git/info/attributes, which is local and not committed. Existing lines are kept.
    Returns the marker the driver of this call prints when it crashes.
    """
    marker = f"{DRIVER_FAILED}-{secrets.token_hex(8)}"
    _git(data_dir, "config", "--local", f"merge.{MERGE_DRIVER}.name", "Chronicle: Feldebene")
    _git(data_dir, "config", "--local", f"merge.{MERGE_DRIVER}.driver",
         _driver_command(data_dir, marker))
    attributes = data_dir / _git(data_dir, "rev-parse", "--git-path", "info/attributes").strip()
    existing = attributes.read_text(encoding="utf-8") if attributes.exists() else ""
    wanted = [f"*{suffix} merge={MERGE_DRIVER}" for suffix in TOPIC_SUFFIXES]
    missing = [line for line in wanted if line not in existing.splitlines()]
    if missing:
        attributes.parent.mkdir(parents=True, exist_ok=True)
        attributes.write_text(existing + "".join(f"{line}\n" for line in missing), encoding="utf-8")
    return marker


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
    result = _run_network(data_dir, "fetch", "--quiet", REMOTE)
    if result.returncode != 0:
        raise SyncError("Der Remote ist nicht erreichbar. Lokal bleibt alles erhalten, der Abgleich lässt sich später "
                        f"wiederholen.\n{result.stderr.strip()}")


def _ref_exists(data_dir: Path, ref: str) -> bool:
    return _run(data_dir, "rev-parse", "--verify", "--quiet", ref).returncode == 0


def _push(data_dir: Path, branch: str) -> None:
    result = _run_network(data_dir, "push", "--quiet", "-u", REMOTE, f"HEAD:refs/heads/{branch}")
    if result.returncode != 0:
        raise SyncError("Hochladen ist fehlgeschlagen. Die Änderungen sind lokal committet und bleiben erhalten.\n"
                        f"{result.stderr.strip()}")
