"""Abgleichen, Vorschau (issue #90): the changes against the last commit of the data folder, before anything is pushed."""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from chronicle import sync

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


def git(folder: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(folder), *args], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def folder(tmp_path, monkeypatch):
    for name, value in {"GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                        "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@example.invalid"}.items():
        monkeypatch.setenv(name, value)
    data = tmp_path / "data"
    shutil.copytree(DATA_DIR, data)
    (data / "b.yaml").write_text(B_TOPIC, encoding="utf-8")
    git(data, "init", "-q", "-b", "main")
    git(data, "add", ".")
    git(data, "commit", "-q", "-m", "Stand")
    return data


def edit(folder: Path, name: str, old: str, new: str) -> None:
    path = folder / name
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, old
    path.write_text(text.replace(old, new), encoding="utf-8")


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
    from chronicle.api import create_app
    from chronicle.db import connect

    conn = connect(tmp_path / "chronicle.db")
    accounts.create_user(conn, "anna", security.hash_password(PASSWORD), "analyst")
    conn.close()
    client = TestClient(create_app(tmp_path / "chronicle.db"))
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


def remote_of(tmp_path, folder):
    """A bare remote that holds the folder's history, with the folder set up to push to it."""
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    git(folder, "remote", "add", "origin", str(remote))
    git(folder, "push", "-q", "-u", "origin", "main")
    return remote


def clone_of(tmp_path, remote, name):
    path = tmp_path / name
    subprocess.run(["git", "clone", "-q", str(remote), str(path)], capture_output=True, text=True, check=True)
    return path


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


def test_remote_changes_to_other_fields_of_an_event_are_merged_with_the_driver(tmp_path, folder, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
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


def test_the_same_field_changed_on_both_sides_is_a_conflict_and_nothing_is_pushed(tmp_path, folder, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
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


def test_an_unreachable_remote_keeps_the_local_commit(tmp_path, folder):
    remote_of(tmp_path, folder)
    git(folder, "remote", "set-url", "origin", str(tmp_path / "gibt-es-nicht.git"))
    edit(folder, "timeline.yaml", 'action: "CVE-2026-XXXX veröffentlicht"', 'action: "Offline"')

    with pytest.raises(sync.SyncError, match="nicht erreichbar"):
        sync.synchronise(folder)

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


def test_the_sync_endpoint_takes_in_the_remote_and_rebuilds_the_index(tmp_path, folder, monkeypatch, api_client):
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    remote = remote_of(tmp_path, folder)
    other = clone_of(tmp_path, remote, "anna")
    edit(other, "timeline.yaml", "    details: |\n      Herstellerhinweis", "    details: |\n      Von Anna\n      Herstellerhinweis")
    assert sync.synchronise(other).committed
    client = api_client()

    response = client.post("/api/v1/sync")

    assert response.status_code == 200, response.text
    assert response.json()["merged"] is True
    assert "Von Anna" in client.get("/api/v1/events/feed-cve").json()["details"]


def test_the_sync_endpoint_reports_a_conflict_without_pushing(tmp_path, folder, monkeypatch, api_client):
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
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


def test_the_merge_driver_does_not_import_code_from_the_data_folder(tmp_path, folder, monkeypatch):
    # git starts the driver with the data folder as working directory. A yaml.py there must not run.
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    remote = remote_of(tmp_path, folder)
    other = clone_of(tmp_path, remote, "anna")
    edit(other, "timeline.yaml", 'details: |\n      Herstellerhinweis', 'details: |\n      Anna Notiz\n      Herstellerhinweis')
    assert sync.synchronise(other).committed
    edit(folder, "timeline.yaml", "status: resolved\n    details: |", "status: open\n    details: |")
    git(folder, "commit", "-q", "-am", "lokal")
    git(folder, "fetch", "-q", "origin")
    marker = tmp_path / "marker"
    (folder / "yaml.py").write_text(f"open({str(marker)!r}, 'w').close()\n", encoding="utf-8")
    sync._use_merge_driver(folder)

    merge = subprocess.run(["git", "-C", str(folder), "merge", "--no-edit", "origin/main"],
                           capture_output=True, text=True, check=False)

    assert not marker.exists()
    assert merge.returncode == 0, merge.stdout + merge.stderr
    assert "Anna Notiz" in (folder / "timeline.yaml").read_text(encoding="utf-8")


def test_a_symlink_in_the_merged_state_is_refused_and_undone(tmp_path, folder):
    remote = remote_of(tmp_path, folder)
    other = clone_of(tmp_path, remote, "anna")
    (tmp_path / "geheim.yaml").write_text("topic: {key: x, title: GEHEIMER-INHALT}\n", encoding="utf-8")
    (other / "link.yaml").symlink_to(tmp_path / "geheim.yaml")
    git(other, "add", "link.yaml")
    git(other, "commit", "-q", "-m", "Link")
    git(other, "push", "-q", "origin", "main")
    edit(folder, "timeline.yaml", 'action: "CVE-2026-XXXX veröffentlicht"', 'action: "Neu"')

    result = sync.synchronise(folder)

    assert result.problems and "link.yaml" in result.problems[0]
    assert "GEHEIMER-INHALT" not in " ".join(result.problems)
    assert not (folder / "link.yaml").exists()
    assert subjects(folder)[0].startswith("Abgleich")  # the local commit stays, the merge is undone
    assert git(remote, "log", "--format=%s", "main").splitlines()[0] == "Link"  # nothing was pushed
