"""Build the index from the topic files: python -m chronicle.importer --db ... --data ..."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .check import load_named_folder
from .db import connect
from .loader import CheckFailed, TimelineError
from .repository import ImportReport, build_index


def import_folder(db_path: str | Path, data_dir: str | Path) -> list[ImportReport]:
    """Make the database match every topic file below data_dir (ADR 0008). Nothing is written if a check fails.

    The checks run before the database is opened, so a failed check leaves no database file behind.
    """
    data = Path(data_dir)
    named = load_named_folder(data, known_keys=_known_event_keys(db_path))
    conn = connect(db_path)
    try:
        return build_index(conn, data, named)
    finally:
        conn.close()


def _known_event_keys(db_path: str | Path) -> set[str]:
    """Keys already in the database. None if there is no database yet, so a failed check leaves no file behind."""
    if not Path(db_path).exists():
        return set()
    conn = connect(db_path)
    try:
        return {row["key"] for row in conn.execute("SELECT key FROM events")}
    finally:
        conn.close()


def print_reports(reports: list[ImportReport]) -> None:
    for report in reports:
        print(f"Topic importiert: {report.topic}")
        print(f"  angelegt: {len(report.created)}, aktualisiert: {len(report.updated)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chronicle.importer", description="Import timeline.yaml into the database.")
    parser.add_argument("--db", required=True, type=Path, help="SQLite file, created if missing")
    parser.add_argument("--data", type=Path, default=Path("/data"), help="folder searched for topic YAML files")
    args = parser.parse_args(argv)

    try:
        reports = import_folder(args.db, args.data)
    except CheckFailed as exc:
        for problem in exc.problems:
            print(f"Fehler: {problem}", file=sys.stderr)
        return 1
    except TimelineError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1

    print_reports(reports)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
