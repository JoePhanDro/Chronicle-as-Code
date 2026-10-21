"""Export the database as YAML topic files that the importer reads again (ADR 0005).

    python -m chronicle.exporter --db /db/chronicle.db --out /export

One file per topic, in the format of timeline.yaml. Evidence contents go to evidence/<event key>/.
Accounts, sessions, the audit log and deleted events are not exported; chronicle.backup copies those.
"""

from __future__ import annotations

import argparse
import posixpath
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import repository
from .db import connect
from .evidence import display_name

EVIDENCE_DIR = "evidence"
HEADER = "# Exportiert aus Chronicle. Evidence liegt daneben unter evidence/.\n"


class ExportError(Exception):
    """The database cannot be exported completely."""


class _Dumper(yaml.SafeDumper):
    """Writes multi-line text as literal blocks, so Markdown stays readable in the file."""


def _represent_text(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|" if "\n" in data else None)


_Dumper.add_representer(str, _represent_text)


@dataclass
class ExportReport:
    topics: int = 0
    events: int = 0
    evidence: int = 0


def export_database(db_path: str | Path, out_dir: str | Path) -> ExportReport:
    if not Path(db_path).is_file():
        raise ExportError(f"Datenbank nicht gefunden: {db_path}")

    conn = connect(db_path)
    try:
        files, report = _build(conn)
    finally:
        conn.close()

    # Everything is built in memory first, so a missing evidence content leaves the output untouched.
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for relative, content in files.items():
        target = out / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return report


def _build(conn) -> tuple[dict[str, bytes], ExportReport]:
    files: dict[str, bytes] = {}
    report = ExportReport()
    for topic in repository.list_topics(conn):
        document, evidence = _topic_document(conn, topic, report)
        files.update(evidence)
        files[f"{topic['key']}.yaml"] = _yaml_bytes(document)
        report.topics += 1
    return files, report


def topic_document(conn, key: str, *, expand_refs: bool = False) -> tuple[dict, dict[str, bytes]] | None:
    """One topic in the import format, plus the evidence contents it refers to. Used for reports.

    With expand_refs, events from other topics that are linked to this one are written out in full
    instead of as keys.
    """
    topic = repository.get_topic(conn, key)
    if topic is None:
        return None
    return _topic_document(conn, topic, ExportReport(), expand_refs=expand_refs)


def _topic_document(
    conn, topic: dict, report: ExportReport, *, expand_refs: bool = False
) -> tuple[dict, dict[str, bytes]]:
    files: dict[str, bytes] = {}
    events = []
    refs = []
    for event in repository.list_topic_events(conn, topic["key"]):
        if event["home_topic"] == topic["key"]:
            events.append(_event_document(conn, event, files, report))
        elif expand_refs:
            events.append(_event_document(conn, repository.get_event(conn, event["key"]), files, report))
        else:
            refs.append(event["key"])

    document = {
        "topic": {"key": topic["key"], "title": topic["title"], "kind": topic["kind"], "summary": topic["summary"]},
    }
    if events:
        document["events"] = sorted(events, key=lambda item: (item["t0"], item["key"]))
    if refs:
        document["refs"] = sorted(refs)
    return document, files


def _yaml_bytes(document: dict) -> bytes:
    text = yaml.dump(document, Dumper=_Dumper, allow_unicode=True, sort_keys=False, width=float("inf"))
    return (HEADER + text).encode("utf-8")


def _event_document(conn, event: dict, files: dict[str, bytes], report: ExportReport) -> dict:
    document = {
        "key": event["key"],
        "t0": event["t0"],
        "phase": event["phase"],
        "actor": event["actor"],
    }
    if event["system"]:
        document["system"] = event["system"]
    document["action"] = event["action"]
    document["status"] = event["status"]
    if event["triggered_by"]:
        document["triggered_by"] = event["triggered_by"]
    if event["details"]:
        document["details"] = event["details"]

    used: set[str] = set()
    evidence = []
    for item in event["evidence"]:
        content = repository.get_evidence_content(conn, item["id"])["content"]
        if content is None:
            raise ExportError(
                f"Evidence {item['file']!r} von {event['key']!r} hat keinen gespeicherten Inhalt. "
                "Bitte den Import erneut ausführen."
            )
        relative = _evidence_path(event["key"], item["file"], used)
        files[relative] = content
        entry = {"file": relative, "type": item["type"]}
        if item["description"]:
            entry["description"] = item["description"]
        evidence.append(entry)
        report.evidence += 1
    if evidence:
        document["evidence"] = evidence

    report.events += 1
    return document


def _evidence_path(event_key: str, file: str, used: set[str]) -> str:
    """evidence/<event key>/<file name>. A counter is added when two files of one event share a name."""
    name = display_name(posixpath.basename(file))
    stem, suffix = posixpath.splitext(name)
    candidate, counter = name, 1
    while candidate in used:
        counter += 1
        candidate = f"{stem}-{counter}{suffix}"
    used.add(candidate)
    return f"{EVIDENCE_DIR}/{event_key}/{candidate}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chronicle.exporter", description="Export topics and events as YAML files.")
    parser.add_argument("--db", required=True, type=Path, help="SQLite file to read")
    parser.add_argument("--out", required=True, type=Path, help="folder for the YAML files and evidence")
    args = parser.parse_args(argv)

    try:
        report = export_database(args.db, args.out)
    except ExportError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1

    print(f"Export nach {args.out}: {report.topics} Topics, {report.events} Events, {report.evidence} Evidence-Dateien")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
