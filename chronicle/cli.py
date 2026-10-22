"""The chronicle command: python -m chronicle check <ordner> (ADR 0009).

Commands work on the files alone. They need neither the database nor a running server.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .check import check_folder
from .loader import find_topic_files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chronicle", description="Work with a chronicle folder of topic files.")
    commands = parser.add_subparsers(dest="command", required=True)

    check = commands.add_parser("check", help="check the topic files; writes nothing")
    check.add_argument("data", type=Path, help="folder with the topic YAML files")
    check.add_argument(
        "--ohne-anhaenge",
        action="store_true",
        help="do not require the attachments to exist (for CI, where they are not in git)",
    )

    args = parser.parse_args(argv)
    if args.command == "check":
        return _check(args.data, evidence=not args.ohne_anhaenge)
    return 2  # unreachable: argparse requires a command


def _check(data: Path, *, evidence: bool) -> int:
    problems = check_folder(data, evidence=evidence)
    for problem in problems:
        print(f"Fehler: {problem}", file=sys.stderr)
    if problems:
        return 1
    print(f"OK: {len(find_topic_files(data))} Topic-Datei(en) geprüft, keine Probleme gefunden.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
