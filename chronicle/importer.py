"""Import a timeline.yaml into the database: python -m chronicle.importer --db ... --data ..."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .db import connect
from .loader import TimelineError, find_topic_files, load_evidence, load_timeline
from .repository import ImportReport, import_timelines


def import_folder(db_path: str | Path, data_dir: str | Path) -> list[ImportReport]:
    """Import every topic file below data_dir. Nothing is written if any file fails."""
    items = []
    for path in find_topic_files(Path(data_dir)):
        timeline = load_timeline(path)
        items.append((timeline, load_evidence(timeline, path.parent)))  # reads and checks evidence first
    conn = connect(db_path)
    try:
        return import_timelines(conn, items)
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chronicle.importer", description="Import timeline.yaml into the database.")
    parser.add_argument("--db", required=True, type=Path, help="SQLite file, created if missing")
    parser.add_argument("--data", type=Path, default=Path("/data"), help="folder searched for topic YAML files")
    args = parser.parse_args(argv)

    try:
        reports = import_folder(args.db, args.data)
    except TimelineError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1

    for report in reports:
        print(f"Topic importiert: {report.topic}")
        print(f"  angelegt: {len(report.created)}, aktualisiert: {len(report.updated)}, übersprungen: {len(report.skipped)}")
        for key, reason in report.skipped:
            print(f"  übersprungen {key}: {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
