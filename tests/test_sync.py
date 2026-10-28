"""Abgleichen, Vorschau (issue #90): the changes against the last commit of the data folder, before anything is pushed."""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from tests.conftest import app_for
from chronicle import sync
from tests.gitutil import clone_of, edit, git, remote_of

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
B_TOPIC = (
    "topic:\n  key: b\n  title: B\nevents:\n  - key: b1\n    t0: 2026-10-14T10:00:00+02:00\n"
    "    phase: Analysis\n    actor: X\n    action: Schritt B\n    status: open\n"
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git ist nicht installiert")


@pytest.fixture(autouse=True)
def plain_http(monkeypatch):
    # The test client talks plain HTTP, so it would not send a session cookie marked Secure.
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")


@pytest.fixture
def folder(git_folder):
    return git_folder


def test_nothing_changed_gives_an_empty_plan(folder):
    result = sync.preview(folder)

    assert result.problems == []
    assert result.changes == []


def test_an_edited_event_is_reported_as_changed(folder):
    edit(folder, "timeline.yaml", 'action: "CVE-2026-XXXX veröffentlicht"', 'action: "Neu formuliert"')

    assert sync.preview(folder).changes == [sync.Change("geändert", "feed-cve", "timeline.yaml")]


def test_a_new_event_is_reported_as_added(folder):
    with (folder / "timeline.yaml").open("a", encoding="utf-8") as handle:
        handle.write("  - key: neu\n    t0: 2026-10-27T10:00:00+02:00\n    phase: Activity\n"
                     "    actor: Analyst\n    action: Neu\n    status: open\n")

    assert sync.preview(folder).changes == [sync.Change("angelegt", "neu", "timeline.yaml")]


def test_a_removed_event_is_reported_with_the_file_it_was_in(folder):
    edit(folder, "b.yaml", "  - key: b1", "  - key: b1-alt")  # rename the key: one removed, one added
    result = sync.preview(folder)

    assert sync.Change("entfernt", "b1", "b.yaml") in result.changes
    assert sync.Change("angelegt", "b1-alt", "b.yaml") in result.changes


def test_an_event_moved_to_another_file_is_reported_as_moved(folder):
    main = yaml.safe_load((folder / "timeline.yaml").read_text(encoding="utf-8"))
    moved = next(item for item in main["events"] if item["key"] == "lessons")
    main["events"].remove(moved)
    (folder / "timeline.yaml").write_text(yaml.safe_dump(main, allow_unicode=True, sort_keys=False), encoding="utf-8")
    other = yaml.safe_load(B_TOPIC)
    other["events"].append(moved)
    (folder / "b.yaml").write_text(yaml.safe_dump(other, allow_unicode=True, sort_keys=False), encoding="utf-8")

    changes = sync.preview(folder).changes

    assert changes == [sync.Change("verschoben", "lessons", "b.yaml")]


def test_a_changed_topic_title_is_reported(folder):
    edit(folder, "b.yaml", "title: B", "title: Bravo")

    assert sync.Change("Topic geändert", "b", "b.yaml") in sync.preview(folder).changes


def test_a_new_topic_file_is_reported_with_its_events(folder):
    (folder / "c.yaml").write_text(B_TOPIC.replace("key: b\n", "key: c\n").replace("title: B", "title: C")
                                   .replace("key: b1", "key: c1"), encoding="utf-8")

    changes = sync.preview(folder).changes

    assert sync.Change("Topic angelegt", "c", "c.yaml") in changes
    assert sync.Change("angelegt", "c1", "c.yaml") in changes


def test_a_broken_file_gives_problems_and_no_plan(folder):
    (folder / "b.yaml").write_text("topic: [kaputt\n", encoding="utf-8")

    result = sync.preview(folder)

    assert result.changes == []
    assert result.problems


def test_a_folder_that_is_not_the_repository_root_is_refused(tmp_path, folder):
    outer = tmp_path / "outer"
    outer.mkdir()
    git(outer, "init", "-q")
    nested = outer / "data"
    shutil.copytree(folder, nested, ignore=shutil.ignore_patterns(".git"))

    with pytest.raises(sync.SyncError, match="Wurzelverzeichnis"):
        sync.preview(nested)


def test_before_the_first_commit_every_event_is_added(tmp_path, monkeypatch):
    for name, value in {"GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                        "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@example.invalid"}.items():
        monkeypatch.setenv(name, value)
    data = tmp_path / "data"
    shutil.copytree(DATA_DIR, data)
    git(data, "init", "-q", "-b", "main")

    changes = sync.preview(data).changes

    assert {change.action for change in changes} == {"angelegt", "Topic angelegt"}


PASSWORD = "correct horse battery"


def test_the_preview_endpoint_lists_the_changes(tmp_path, folder, api_client):
    edit(folder, "timeline.yaml", 'action: "CVE-2026-XXXX veröffentlicht"', 'action: "Neu formuliert"')
    client = api_client()

    response = client.get("/api/v1/sync/preview")

    assert response.status_code == 200, response.text
    assert response.json() == {"problems": [],
                               "changes": [{"action": "geändert", "key": "feed-cve", "file": "timeline.yaml"}]}


def test_the_preview_endpoint_reports_problems_instead_of_a_plan(tmp_path, folder, api_client):
    (folder / "b.yaml").write_text("topic: [kaputt\n", encoding="utf-8")
    client = api_client()

    response = client.get("/api/v1/sync/preview")

    assert response.status_code == 200, response.text
    assert response.json()["changes"] == []
    assert response.json()["problems"]


def test_a_viewer_cannot_preview(tmp_path, folder, api_client):
    client = api_client(role="viewer")

    assert client.get("/api/v1/sync/preview").status_code == 403


def test_without_a_data_folder_the_preview_is_refused(tmp_path):
    from fastapi.testclient import TestClient

    from chronicle import accounts, security
    from chronicle.db import connect

    conn = connect(tmp_path / "chronicle.db")
    accounts.create_user(conn, "anna", security.hash_password(PASSWORD), "analyst")
    conn.close()
    client = TestClient(app_for(tmp_path / "chronicle.db"))
    response = client.post("/api/v1/auth/login", json={"username": "anna", "password": PASSWORD})
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]

    assert client.get("/api/v1/sync/preview").status_code == 409


def test_a_committed_but_unpushed_change_is_still_reported(tmp_path, folder):
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    git(folder, "remote", "add", "origin", str(remote))
    git(folder, "push", "-q", "-u", "origin", "main")
    edit(folder, "timeline.yaml", 'action: "CVE-2026-XXXX veröffentlicht"', 'action: "Lokal committet"')
    git(folder, "commit", "-q", "-am", "Lokal")

    assert sync.Change("geändert", "feed-cve", "timeline.yaml") in sync.preview(folder).changes


def test_an_upstream_without_local_changes_gives_an_empty_plan(tmp_path, folder):
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    git(folder, "remote", "add", "origin", str(remote))
    git(folder, "push", "-q", "-u", "origin", "main")

    assert sync.preview(folder).changes == []


ROOT = Path(__file__).resolve().parent.parent


def subjects(folder: Path) -> list[str]:
    return git(folder, "log", "--format=%s").splitlines()


def test_a_local_change_is_committed_and_pushed(tmp_path, folder):
    remote = remote_of(tmp_path, folder)
    edit(folder, "timeline.yaml", 'action: "CVE-2026-XXXX veröffentlicht"', 'action: "Neu"')

    result = sync.synchronise(folder)

    assert result == sync.SyncResult(committed=True, merged=False, conflicts=[], problems=[])
    assert subjects(remote)[0].startswith("Abgleich ")


def test_nothing_to_do_commits_and_pushes_nothing(tmp_path, folder):
    remote_of(tmp_path, folder)
    before = subjects(folder)

    result = sync.synchronise(folder)

    assert result.committed is False
    assert subjects(folder) == before


def test_remote_changes_to_other_fields_of_an_event_are_merged_with_the_driver(tmp_path, folder):
    remote = remote_of(tmp_path, folder)
    other = clone_of(tmp_path, remote, "anna")
    edit(other, "timeline.yaml", 'details: |\n      Herstellerhinweis', 'details: |\n      Anna Notiz\n      Herstellerhinweis')
    assert sync.synchronise(other).committed
    edit(folder, "timeline.yaml", "status: resolved\n    details: |", "status: open\n    details: |")  # the feed-cve status, locally

    result = sync.synchronise(folder)

    assert result.merged and result.conflicts == []
    feed_cve = next(item for item in yaml.safe_load((folder / "timeline.yaml").read_text(encoding="utf-8"))["events"]
                    if item["key"] == "feed-cve")
    assert feed_cve["status"] == "open"  # our change
    assert "Anna Notiz" in feed_cve["details"]  # the other side's change


def test_the_same_field_changed_on_both_sides_is_a_conflict_and_nothing_is_pushed(tmp_path, folder):
    remote = remote_of(tmp_path, folder)
    other = clone_of(tmp_path, remote, "anna")
    edit(other, "timeline.yaml", 'action: "CVE-2026-XXXX veröffentlicht"', 'action: "Anna"')
    assert sync.synchronise(other).committed
    edit(folder, "timeline.yaml", 'action: "CVE-2026-XXXX veröffentlicht"', 'action: "Bernd"')
    remote_before = git(remote, "rev-parse", "main").strip()

    result = sync.synchronise(folder)

    assert result.conflicts == ["timeline.yaml"]
    assert result.merged is False
    assert git(remote, "rev-parse", "main").strip() == remote_before
    assert not (folder / ".git" / "MERGE_HEAD").exists()


def test_a_conflict_carries_both_sides_of_the_file(tmp_path, folder):
    remote = remote_of(tmp_path, folder)
    other = clone_of(tmp_path, remote, "anna")
    edit(other, "timeline.yaml", 'action: "CVE-2026-XXXX veröffentlicht"', 'action: "Anna"')
    sync.synchronise(other)
    edit(folder, "timeline.yaml", 'action: "CVE-2026-XXXX veröffentlicht"', 'action: "Bernd"')

    (side,) = sync.synchronise(folder).sides

    assert side.name == "timeline.yaml"
    assert 'action: "Bernd"' in side.ours and 'action: "Anna"' in side.theirs
    assert '-    action: "Bernd"' in side.diff and '+    action: "Anna"' in side.diff


def test_a_side_without_the_file_is_none(tmp_path, folder):
    remote = remote_of(tmp_path, folder)
    other = clone_of(tmp_path, remote, "anna")
    git(other, "rm", "-q", "timeline.yaml")
    git(other, "commit", "-q", "-m", "Weg")
    git(other, "push", "-q", "origin", "main")
    edit(folder, "timeline.yaml", 'action: "CVE-2026-XXXX veröffentlicht"', 'action: "Bernd"')

    result = sync.synchronise(folder)

    assert result.conflicts == ["timeline.yaml"]
    (side,) = result.sides
    assert side.ours is not None and side.theirs is None and side.diff


def test_an_unreachable_remote_keeps_the_local_commit(tmp_path, folder):
    remote_of(tmp_path, folder)
    git(folder, "remote", "set-url", "origin", str(tmp_path / "gibt-es-nicht.git"))
    edit(folder, "timeline.yaml", 'action: "CVE-2026-XXXX veröffentlicht"', 'action: "Offline"')

    with pytest.raises(sync.SyncError, match="nicht erreichbar"):
        sync.synchronise(folder)

    assert subjects(folder)[0].startswith("Abgleich ")


def test_the_folder_is_not_locked_while_the_remote_is_fetched(tmp_path, folder, monkeypatch):
    """A slow network must not block the writers of the data folder (issue #131)."""
    import threading

    from chronicle.store import folder_lock

    remote_of(tmp_path, folder)
    real_fetch = sync._fetch
    got_the_lock = []

    def fetch_while_a_writer_waits(data_dir):
        def writer():
            with folder_lock(data_dir):
                got_the_lock.append(True)

        thread = threading.Thread(target=writer)
        thread.start()
        thread.join(timeout=5)
        real_fetch(data_dir)

    monkeypatch.setattr(sync, "_fetch", fetch_while_a_writer_waits)
    sync.synchronise(folder)

    assert got_the_lock == [True]


def test_a_change_made_while_the_remote_is_fetched_is_synchronised_too(tmp_path, folder, monkeypatch):
    remote = remote_of(tmp_path, folder)
    real_fetch = sync._fetch

    def fetch_and_edit(data_dir):
        real_fetch(data_dir)
        edit(data_dir, "timeline.yaml", 'action: "CVE-2026-XXXX veröffentlicht"', 'action: "Währenddessen"')

    monkeypatch.setattr(sync, "_fetch", fetch_and_edit)
    result = sync.synchronise(folder)

    assert result.committed is True and result.conflicts == [] and result.problems == []
    assert "Währenddessen" in git(remote, "show", "main:timeline.yaml")


def test_a_remote_that_does_not_answer_is_stopped_and_keeps_the_local_commit(tmp_path, folder, monkeypatch):
    remote_of(tmp_path, folder)
    edit(folder, "timeline.yaml", 'action: "CVE-2026-XXXX veröffentlicht"', 'action: "Offline"')
    real_run = subprocess.run
    seen = {}

    def hanging_fetch(command, **kwargs):
        if "fetch" in command:
            seen.update(kwargs)
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return real_run(command, **kwargs)

    monkeypatch.setattr(sync.subprocess, "run", hanging_fetch)
    with pytest.raises(sync.SyncError, match="antwortet nicht"):
        sync.synchronise(folder)

    assert seen["timeout"] == sync.NETWORK_TIMEOUT_SECONDS
    assert seen["env"]["GIT_TERMINAL_PROMPT"] == "0"  # git never waits for a password nobody can type
    assert subjects(folder)[0].startswith("Abgleich ")


def test_broken_files_stop_the_sync_before_anything_is_committed(tmp_path, folder):
    remote_of(tmp_path, folder)
    (folder / "b.yaml").write_text("topic: [kaputt\n", encoding="utf-8")
    before = subjects(folder)

    result = sync.synchronise(folder)

    assert result.problems
    assert subjects(folder) == before


def test_the_first_upload_creates_the_branch_on_the_remote(tmp_path, folder):
    remote = tmp_path / "leer.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    git(folder, "remote", "add", "origin", str(remote))

    result = sync.synchronise(folder)

    assert result.committed is False
    assert git(remote, "rev-parse", "--verify", "main").strip()


def test_the_sync_endpoint_takes_in_the_remote_and_rebuilds_the_index(tmp_path, folder, api_client):
    remote = remote_of(tmp_path, folder)
    other = clone_of(tmp_path, remote, "anna")
    edit(other, "timeline.yaml", "    details: |\n      Herstellerhinweis", "    details: |\n      Von Anna\n      Herstellerhinweis")
    assert sync.synchronise(other).committed
    client = api_client()

    response = client.post("/api/v1/sync")

    assert response.status_code == 200, response.text
    assert response.json()["merged"] is True
    assert "Von Anna" in client.get("/api/v1/events/feed-cve").json()["details"]


def test_the_sync_endpoint_reports_a_conflict_without_pushing(tmp_path, folder, api_client):
    remote = remote_of(tmp_path, folder)
    other = clone_of(tmp_path, remote, "anna")
    edit(other, "timeline.yaml", 'action: "CVE-2026-XXXX veröffentlicht"', 'action: "Anna"')
    assert sync.synchronise(other).committed
    edit(folder, "timeline.yaml", 'action: "CVE-2026-XXXX veröffentlicht"', 'action: "Bernd"')
    client = api_client()

    response = client.post("/api/v1/sync")

    assert response.status_code == 200, response.text
    assert response.json()["conflicts"] == ["timeline.yaml"]


def test_a_viewer_cannot_sync(tmp_path, folder, api_client):
    remote_of(tmp_path, folder)
    client = api_client(role="viewer")

    assert client.post("/api/v1/sync").status_code == 403


def diverged(tmp_path, folder):
    """The remote and the folder changed different fields of one event: the merge needs the driver."""
    remote = remote_of(tmp_path, folder)
    other = clone_of(tmp_path, remote, "anna")
    edit(other, "timeline.yaml", 'details: |\n      Herstellerhinweis', 'details: |\n      Anna Notiz\n      Herstellerhinweis')
    assert sync.synchronise(other).committed
    edit(folder, "timeline.yaml", "status: resolved\n    details: |", "status: open\n    details: |")
    return remote, other


def push_from(other, message, *, stage=True):
    if stage:
        git(other, "add", "-A")
    git(other, "commit", "-q", "-m", message)
    git(other, "push", "-q", "origin", "main")


ATTACKS = {
    "yaml.py": "yaml.py", "pydantic.py": "pydantic.py", "ruamel.py": "ruamel.py",
    "yaml-package": "yaml/__init__.py", "chronicle-package": "chronicle/__init__.py",
}


@pytest.mark.parametrize("path", ATTACKS.values(), ids=ATTACKS.keys())
def test_the_merge_driver_does_not_import_code_from_the_data_folder(tmp_path, folder, path):
    # git starts the driver with the data folder as working directory. Code planted there must not run.
    diverged(tmp_path, folder)
    git(folder, "commit", "-q", "-am", "lokal")
    git(folder, "fetch", "-q", "origin")
    marker = tmp_path / "marker"
    (folder / path).parent.mkdir(exist_ok=True)
    (folder / path).write_text(f"open({str(marker)!r}, 'w').close()\n", encoding="utf-8")
    sync._use_merge_driver(folder)

    merge = subprocess.run(["git", "-C", str(folder), "merge", "--no-edit", "origin/main"],
                           capture_output=True, text=True, check=False)

    assert not marker.exists()
    assert merge.returncode == 0, merge.stdout + merge.stderr
    assert "Anna Notiz" in (folder / "timeline.yaml").read_text(encoding="utf-8")


def test_the_merge_driver_keeps_the_environment_of_the_app(tmp_path, folder, monkeypatch):
    # A deployment may keep its dependencies in PYTHONPATH or the user site. Only the data folder is left out.
    extra = tmp_path / "extra"
    extra.mkdir()
    marker = tmp_path / "from-environment"
    (extra / "sitecustomize.py").write_text(f"open({str(marker)!r}, 'w').close()\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(extra))
    diverged(tmp_path, folder)

    assert sync.synchronise(folder).merged
    assert marker.exists()


def test_a_crashing_merge_driver_is_an_error_and_not_a_conflict(tmp_path, folder, monkeypatch):
    broken = tmp_path / "broken"
    (broken / "chronicle").mkdir(parents=True)
    (broken / "chronicle" / "__init__.py").write_text("", encoding="utf-8")
    (broken / "chronicle" / "merge.py").write_text("raise RuntimeError('kaputt')\n", encoding="utf-8")
    monkeypatch.setattr(sync, "PACKAGE_ROOT", broken)
    diverged(tmp_path, folder)

    with pytest.raises(sync.SyncError, match="abgestürzt.*kaputt"):
        sync.synchronise(folder)

    assert not (folder / ".git" / "MERGE_HEAD").exists()


def test_a_percent_sign_in_the_path_of_the_app_does_not_break_the_driver_line(tmp_path, folder, monkeypatch):
    package_root = tmp_path / "per%Acent"
    shutil.copytree(ROOT / "chronicle", package_root / "chronicle", ignore=shutil.ignore_patterns("__pycache__", "static"))
    monkeypatch.setattr(sync, "PACKAGE_ROOT", package_root)
    diverged(tmp_path, folder)

    result = sync.synchronise(folder)

    assert result.merged and result.conflicts == []


def link_to_remote(other, tmp_path, name="link.yaml"):
    (tmp_path / "geheim.yaml").write_text("topic: {key: x, title: GEHEIMER-INHALT}\n", encoding="utf-8")
    (other / name).symlink_to(tmp_path / "geheim.yaml")
    push_from(other, "Link")


def test_a_symlink_in_the_merged_state_is_refused_and_undone(tmp_path, folder):
    remote = remote_of(tmp_path, folder)
    other = clone_of(tmp_path, remote, "anna")
    link_to_remote(other, tmp_path)
    edit(folder, "timeline.yaml", 'action: "CVE-2026-XXXX veröffentlicht"', 'action: "Neu"')

    result = sync.synchronise(folder)

    assert result.problems and "link.yaml" in result.problems[0]
    assert "GEHEIMER-INHALT" not in " ".join(result.problems)
    assert not (folder / "link.yaml").exists()
    assert subjects(folder)[0].startswith("Abgleich")  # the local commit stays, the merge is undone
    assert git(remote, "log", "--format=%s", "main").splitlines()[0] == "Link"  # nothing was pushed


def test_a_submodule_and_a_path_with_a_newline_in_the_merged_state_are_refused(tmp_path, folder):
    remote = remote_of(tmp_path, folder)
    other = clone_of(tmp_path, remote, "anna")
    sha = git(other, "rev-parse", "HEAD").strip()
    git(other, "update-index", "--add", "--cacheinfo", f"160000,{sha},sub")
    (other / "x\ny").symlink_to(tmp_path)
    git(other, "add", "x\ny")
    push_from(other, "Submodul und Link", stage=False)
    edit(folder, "timeline.yaml", 'action: "CVE-2026-XXXX veröffentlicht"', 'action: "Neu"')

    result = sync.synchronise(folder)

    assert sorted(problem.split(":")[0] for problem in result.problems) == ["sub", "x\ny"]


def test_the_undo_of_a_refused_merge_keeps_uncommitted_changes_to_other_tracked_files(tmp_path, folder):
    (folder / "NOTES.md").write_text("alt\n", encoding="utf-8")
    git(folder, "add", "NOTES.md")
    git(folder, "commit", "-q", "-m", "Notizen")
    remote = remote_of(tmp_path, folder)
    other = clone_of(tmp_path, remote, "anna")
    link_to_remote(other, tmp_path)
    (folder / "NOTES.md").write_text("meine ungesicherte Notiz\n", encoding="utf-8")

    result = sync.synchronise(folder)

    assert result.problems
    assert (folder / "NOTES.md").read_text(encoding="utf-8") == "meine ungesicherte Notiz\n"


def test_the_undo_of_a_merge_that_fails_the_checks_keeps_uncommitted_changes_to_other_tracked_files(tmp_path, folder):
    (folder / "NOTES.md").write_text("alt\n", encoding="utf-8")
    git(folder, "add", "NOTES.md")
    git(folder, "commit", "-q", "-m", "Notizen")
    remote = remote_of(tmp_path, folder)
    other = clone_of(tmp_path, remote, "anna")
    (other / "kaputt.yaml").write_text("topic: [\n", encoding="utf-8")
    push_from(other, "Kaputt")
    (folder / "NOTES.md").write_text("meine ungesicherte Notiz\n", encoding="utf-8")

    result = sync.synchronise(folder)

    assert result.problems
    assert (folder / "NOTES.md").read_text(encoding="utf-8") == "meine ungesicherte Notiz\n"


def test_a_link_in_the_base_is_skipped_and_never_read(folder, tmp_path):
    (tmp_path / "geheim.txt").write_text("GEHEIMER-INHALT", encoding="utf-8")
    (folder / "evidence" / "l.png").symlink_to(tmp_path / "geheim.txt")
    (folder / "l.yaml").symlink_to(tmp_path / "geheim.txt")
    git(folder, "add", "-A")
    git(folder, "commit", "-q", "-m", "Links")

    assert sorted(sync.base_timelines(folder)) == ["b.yaml", "timeline.yaml"]


def test_the_sync_runs_again_once_the_remote_has_removed_a_link(tmp_path, folder):
    # The preview runs before the fetch, so a refusal on a link in the base would lock every later sync.
    remote = remote_of(tmp_path, folder)
    other = clone_of(tmp_path, remote, "anna")
    link_to_remote(other, tmp_path)
    git(folder, "fetch", "-q", "origin")
    assert sync.preview(folder).problems == []  # the link in the upstream does not block the preview
    first = sync.synchronise(folder)
    assert first.problems and not (folder / "link.yaml").exists()

    git(other, "rm", "-q", "link.yaml")
    push_from(other, "Link entfernt", stage=False)
    edit(folder, "timeline.yaml", 'action: "CVE-2026-XXXX veröffentlicht"', 'action: "Neu"')
    second = sync.synchronise(folder)

    assert second.problems == [] and second.conflicts == []
    assert git(remote, "show", "main:timeline.yaml").count('action: "Neu"') == 1  # the local change was pushed
    assert not (folder / "link.yaml").exists()


def test_a_file_named_like_the_failure_marker_does_not_turn_a_conflict_into_a_crash(tmp_path, folder):
    name = f"{sync.DRIVER_FAILED}.yaml"
    (folder / name).write_text(B_TOPIC.replace("key: b\n", "key: c\n").replace("b1", "c1"), encoding="utf-8")
    git(folder, "add", "-A")
    git(folder, "commit", "-q", "-m", "Datei")
    remote = remote_of(tmp_path, folder)
    other = clone_of(tmp_path, remote, "anna")
    edit(other, name, "Schritt B", "Anna")
    assert sync.synchronise(other).committed
    edit(folder, name, "Schritt B", "Bernd")

    result = sync.synchronise(folder)

    assert result.conflicts == [name]


def broken_package(tmp_path, merge_source):
    root = tmp_path / "broken"
    (root / "chronicle").mkdir(parents=True)
    (root / "chronicle" / "__init__.py").write_text("", encoding="utf-8")
    (root / "chronicle" / "merge.py").write_text(merge_source, encoding="utf-8")
    return root


def test_a_driver_killed_by_a_signal_gives_a_fixed_message_without_the_output_of_git(tmp_path, folder, monkeypatch):
    source = "import os, signal\ndef main(argv):\n    os.kill(os.getpid(), signal.SIGKILL)\n"
    monkeypatch.setattr(sync, "PACKAGE_ROOT", broken_package(tmp_path, source))
    diverged(tmp_path, folder)

    with pytest.raises(sync.SyncError) as caught:
        sync.synchronise(folder)

    assert "unerwartet beendet" in str(caught.value)
    assert "import" not in str(caught.value) and "sys.path" not in str(caught.value)
    assert not (folder / ".git" / "MERGE_HEAD").exists()


def test_the_reported_driver_failure_is_short_and_free_of_control_characters(tmp_path, folder, monkeypatch):
    source = "def main(argv):\n    raise RuntimeError('\\x1b[31m' + 'x' * 1000 + 'ENDE')\n"
    monkeypatch.setattr(sync, "PACKAGE_ROOT", broken_package(tmp_path, source))
    diverged(tmp_path, folder)

    with pytest.raises(sync.SyncError) as caught:
        sync.synchronise(folder)

    assert len(str(caught.value)) < 500
    assert "\x1b" not in str(caught.value) and "ENDE" not in str(caught.value)


@pytest.mark.parametrize("planted", ["yaml.py", "sitecustomize.py"])
def test_relative_and_empty_entries_of_pythonpath_do_not_bring_the_data_folder_in(tmp_path, folder, monkeypatch, planted):
    extra = tmp_path / "extra"
    extra.mkdir()
    marker = tmp_path / "marker"
    monkeypatch.setenv("PYTHONPATH", f":{extra}:.")
    diverged(tmp_path, folder)
    git(folder, "commit", "-q", "-am", "lokal")
    git(folder, "fetch", "-q", "origin")
    (folder / planted).write_text(f"open({str(marker)!r}, 'w').close()\n", encoding="utf-8")
    sync._use_merge_driver(folder)

    merge = subprocess.run(["git", "-C", str(folder), "merge", "--no-edit", "origin/main"],
                           capture_output=True, text=True, check=False)

    assert not marker.exists()
    assert merge.returncode == 0, merge.stdout + merge.stderr


def test_a_lock_file_that_is_a_folder_is_a_sync_error(tmp_path, folder):
    remote_of(tmp_path, folder)
    (folder / ".chronicle.lock").mkdir()

    with pytest.raises(sync.SyncError, match="chronicle.lock"):
        sync.synchronise(folder)
