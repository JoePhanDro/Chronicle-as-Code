"""Import a timeline.yaml into the database: python -m chronicle.importer --db ... --data ..."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .db import connect
from .loader import TimelineError, collect_evidence, find_timeline, load_timeline
from .repository import import_timeline


def import_folder(db_path: str | Path, data_dir: str | Path) -> int:
    timeline_path = find_timeline(Path(data_dir))
    timeline = load_timeline(timeline_path)
    collect_evidence(timeline, timeline_path.parent)  # fails early if evidence files are missing
    conn = connect(db_path)
    try:
        return import_timeline(conn, timeline)
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chronicle.importer", description="Import timeline.yaml into the database.")
    parser.add_argument("--db", required=True, type=Path, help="SQLite file, created if missing")
    parser.add_argument("--data", type=Path, default=Path("/data"), help="folder searched for timeline.yaml")
    args = parser.parse_args(argv)

    try:
        incident_id = import_folder(args.db, args.data)
    except TimelineError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1

    print(f"Incident importiert: id={incident_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
