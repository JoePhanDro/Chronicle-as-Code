"""The chronicle command: check, build and schema (ADR 0009).

Commands work on the files alone. They need neither the database nor a running server.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .build import build_site
from .check import check_folder
from .loader import CheckFailed, TimelineError, find_topic_files
from .schema import schema_text


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

    build = commands.add_parser("build", help="write a static HTML page per topic and an index into a folder")
    build.add_argument("data", type=Path, help="folder with the topic YAML files")
    build.add_argument("--out", type=Path, required=True, help="folder for the HTML pages")

    commands.add_parser("schema", help="print the JSON Schema of a topic file")

    args = parser.parse_args(argv)
    if args.command == "check":
        return _check(args.data, evidence=not args.ohne_anhaenge)
    if args.command == "build":
        return _build(args.data, args.out)
    print(schema_text(), end="")
    return 0


def _check(data: Path, *, evidence: bool) -> int:
    problems = check_folder(data, evidence=evidence)
    for problem in problems:
        print(f"Fehler: {problem}", file=sys.stderr)
    if problems:
        return 1
    print(f"OK: {len(find_topic_files(data))} Topic-Datei(en) geprüft, keine Probleme gefunden.")
    return 0


def _build(data: Path, out: Path) -> int:
    try:
        written = build_site(data, out)
    except CheckFailed as exc:
        for problem in exc.problems:
            print(f"Fehler: {problem}", file=sys.stderr)
        return 1
    except TimelineError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1
    print(f"OK: {len(written) - 1} Topic-Seite(n) und index.html nach {out} geschrieben.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
