"""Git helpers shared by the tests of the synchronisation."""

import subprocess
from pathlib import Path


def git(folder: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(folder), *args], capture_output=True, text=True, check=True).stdout


def edit(folder: Path, name: str, old: str, new: str) -> None:
    path = folder / name
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, old
    path.write_text(text.replace(old, new), encoding="utf-8")


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
