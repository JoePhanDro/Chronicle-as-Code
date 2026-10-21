"""Import a timeline.yaml into the database: python -m chronicle.importer --db ... --data ..."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .db import connect
from .loader import TimelineError, find_timeline, load_evidence, load_timeline
from .repository import ImportReport, import_timeline


def import_folder(db_path: str | Path, data_dir: str | Path) -> ImportReport:
    timeline_path = find_timeline(Path(data_dir))
    timeline = load_timeline(timeline_path)
    blobs = load_evidence(timeline, timeline_path.parent)  # fails before anything is written
    conn = connect(db_path)
    try:
        return import_timeline(conn, timeline, blobs)
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chronicle.importer", description="Import timeline.yaml into the database.")
    parser.add_argument("--db", required=True, type=Path, help="SQLite file, created if missing")
    parser.add_argument("--data", type=Path, default=Path("/data"), help="folder searched for timeline.yaml")
    args = parser.parse_args(argv)

    try:
        report = import_folder(args.db, args.data)
    except TimelineError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1

    print(f"Topic importiert: {report.topic}")
    print(f"  angelegt: {len(report.created)}, aktualisiert: {len(report.updated)}, übersprungen: {len(report.skipped)}")
    for key, reason in report.skipped:
        print(f"  übersprungen {key}: {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
