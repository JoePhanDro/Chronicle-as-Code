"""Attaching and removing evidence files. The file goes to evidence/, the topic file names it (ADR 0004, ADR 0008)."""

from __future__ import annotations

import hashlib
import re
import sqlite3
import stat
from pathlib import Path

from . import accounts
from .errors import Invalid, NotFound
from .evidence import EVIDENCE_TYPES, MAX_DESCRIPTION_LENGTH, Blob
from .folder_writes import all_or_nothing, file_of_event, recorded_version, store_change
from .index import reindex
from .queries import evidence_meta
from .store import (
    StoreError,
    add_evidence_to_file,
    remove_evidence_from_file,
    require_no_link,
    write_new_file,
)


def _free_evidence_name(data_dir: Path, key: str, name: str, sha256: str) -> tuple[str, bool]:
    """A path under evidence/<event key>/ for an attachment, and whether the file still has to be written.

    The name is cleaned to letters, digits, dots, dashes and underscores. An identical file already there is reused;
    a different one with the same name gets a number, so nothing is overwritten.
    """
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "datei"
    stem, dot, extension = safe.rpartition(".") if "." in safe else (safe, "", "")
    try:
        require_no_link(data_dir, data_dir / "evidence" / key)
    except StoreError as exc:
        raise Invalid(str(exc)) from exc
    for number in range(1, 1000):
        candidate = Path("evidence") / key / (safe if number == 1 else f"{stem}-{number}{dot}{extension}")
        target = data_dir / candidate
        try:
            found = target.lstat()
        except FileNotFoundError:
            return candidate.as_posix(), True
        if not stat.S_ISREG(found.st_mode):
            raise Invalid(f"{candidate.as_posix()} ist ein Link oder keine gewöhnliche Datei.")
        if hashlib.sha256(target.read_bytes()).hexdigest() == sha256:
            return candidate.as_posix(), False
    raise Invalid("Zu viele Dateien mit demselben Namen bei diesem Event.")


def _add_evidence_in_folder(
    conn: sqlite3.Connection, key: str, blob: Blob, evidence_type: str, description: str, data_dir: Path,
    actor_id: int, ip: str,
) -> dict:
    if evidence_type not in EVIDENCE_TYPES:
        raise Invalid(f"Unbekannter Evidence-Typ {evidence_type!r}")
    if len(description) > MAX_DESCRIPTION_LENGTH:
        raise Invalid(f"Die Beschreibung darf höchstens {MAX_DESCRIPTION_LENGTH} Zeichen lang sein")
    located = conn.execute("SELECT path FROM event_files WHERE event_key = ?", (key,)).fetchone()
    if located is None:
        raise NotFound(f"Event {key!r} nicht gefunden")
    relative, must_write = _free_evidence_name(data_dir, key, blob.name, blob.sha256)
    if conn.execute(
        "SELECT 1 FROM evidence JOIN events ON events.pk = evidence.event_pk WHERE events.key = ? AND evidence.file = ?",
        (key, relative),
    ).fetchone():
        raise Invalid("Dieser Beleg ist bei dem Event bereits angehängt.")

    # The file goes to disk first and the topic file names it. If the topic file refuses, the file goes again.
    target = data_dir / relative
    item = {"file": relative, "type": evidence_type, "description": description.strip()}
    with all_or_nothing(data_dir) as created:
        if must_write:
            write_new_file(target, blob.content, root=data_dir)
            created.append(target)
        with store_change(located["path"], "Der Beleg wurde nicht angehängt"):
            add_evidence_to_file(data_dir / located["path"], key, item,
                                 expected_version=recorded_version(conn, located["path"]))
        reindex(conn, data_dir)
    accounts.audit(conn, "evidence_added", user_id=actor_id, target=key, detail=relative, ip=ip)
    row = conn.execute(
        "SELECT evidence.pk FROM evidence JOIN events ON events.pk = evidence.event_pk "
        "WHERE events.key = ? AND evidence.file = ?",
        (key, relative),
    ).fetchone()
    return evidence_meta(conn, row["pk"])


def _delete_evidence_in_folder(conn: sqlite3.Connection, evidence_id: int, data_dir: Path, actor_id: int, ip: str) -> None:
    row = conn.execute(
        "SELECT evidence.file, events.key FROM evidence JOIN events ON events.pk = evidence.event_pk WHERE evidence.pk = ?",
        (evidence_id,),
    ).fetchone()
    if row is None:
        raise NotFound("Evidence nicht gefunden")
    path = file_of_event(conn, row["key"])
    try:
        require_no_link(data_dir, (data_dir / row["file"]).parent)
    except StoreError as exc:
        raise Invalid(str(exc)) from exc
    with all_or_nothing(data_dir):
        with store_change(path, "Der Beleg wurde nicht entfernt"):
            remove_evidence_from_file(data_dir / path, row["key"], row["file"],
                                      expected_version=recorded_version(conn, path))
        reindex(conn, data_dir)
    # The file goes last: until the index has accepted the removal, the topic file still names it.
    (data_dir / row["file"]).unlink(missing_ok=True)
    accounts.audit(conn, "evidence_deleted", user_id=actor_id, target=row["key"], detail=row["file"], ip=ip)


def add_evidence(
    conn: sqlite3.Connection, key: str, blob: Blob, evidence_type: str, description: str, actor_id: int, ip: str,
    data_dir: Path,
) -> dict:
    """Attach a checked file to an event: the file goes to evidence/ and the topic file names it."""
    return _add_evidence_in_folder(conn, key, blob, evidence_type, description, data_dir, actor_id, ip)


def delete_evidence(conn: sqlite3.Connection, evidence_id: int, actor_id: int, ip: str, data_dir: Path) -> None:
    _delete_evidence_in_folder(conn, evidence_id, data_dir, actor_id, ip)
