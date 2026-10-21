"""Consistent copy of the database file, made with SQLite's online backup API.

    python -m chronicle.backup --db /db/chronicle.db --out /backup

The copy holds everything: accounts with password hashes, sessions and the audit log.
Store it with the same care as the database itself. The copy is readable only by its owner.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path


class BackupError(Exception):
    """The backup could not be written or did not pass the integrity check."""


def backup_database(db_path: str | Path, target: str | Path) -> Path:
    source = Path(db_path)
    if not source.is_file():
        raise BackupError(f"Datenbank nicht gefunden: {source}")

    destination = Path(target)
    if destination.exists():
        raise BackupError(f"Die Zieldatei existiert bereits: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)

    # Created with owner-only permissions before SQLite writes into it.
    os.close(os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))

    reader = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
    writer = sqlite3.connect(destination)
    try:
        reader.backup(writer)
        verdict = writer.execute("PRAGMA integrity_check").fetchone()[0]
        if verdict != "ok":
            raise BackupError(f"Integritätsprüfung fehlgeschlagen: {verdict}")
    except sqlite3.Error as exc:
        destination.unlink(missing_ok=True)
        raise BackupError(f"Backup fehlgeschlagen: {exc}") from exc
    except BackupError:
        destination.unlink(missing_ok=True)
        raise
    finally:
        writer.close()
        reader.close()
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chronicle.backup", description="Copy the database file safely.")
    parser.add_argument("--db", required=True, type=Path, help="SQLite file to copy")
    parser.add_argument("--out", required=True, type=Path, help="target file, or a folder for a timestamped file")
    args = parser.parse_args(argv)

    target = args.out
    if target.is_dir():
        target = target / f"chronicle-{datetime.now(UTC):%Y%m%d-%H%M%S}.db"

    try:
        written = backup_database(args.db, target)
    except BackupError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1

    print(f"Backup geschrieben: {written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
