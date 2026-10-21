"""Command line entry point: python -m chronicle --data /data --out /output"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .builder import build_site
from .loader import TimelineError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chronicle", description="Render timeline.yaml as an interactive dashboard.")
    parser.add_argument("--data", type=Path, default=Path("/data"), help="folder searched for timeline.yaml")
    parser.add_argument("--out", type=Path, default=Path("/output"), help="folder that receives index.html")
    args = parser.parse_args(argv)

    try:
        index = build_site(args.data, args.out)
    except TimelineError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1

    print(f"Dashboard geschrieben: {index}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
