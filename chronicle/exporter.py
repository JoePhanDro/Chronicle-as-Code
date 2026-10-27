"""Export the database as YAML topic files that the importer reads again (ADR 0005).

    python -m chronicle.exporter --db /db/chronicle.db --out /export

One file per topic, in the format of timeline.yaml. Evidence contents go to evidence/<event key>/.
Accounts, sessions, the audit log and deleted events are not exported; chronicle.backup copies those.
"""

from __future__ import annotations

import argparse
import posixpath
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import repository
from .db import connect
from .evidence import display_name
from .schema import SCHEMA_URL
from .yamlscan import MAX_YAML_BYTES

EVIDENCE_DIR = "evidence"
HEADER = f"# yaml-language-server: $schema={SCHEMA_URL}\n# Exportiert aus Chronicle. Evidence liegt daneben unter evidence/.\n"


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
    too_large: list[str] = field(default_factory=list)  # files the app would refuse to read back (size limit)


def export_database(db_path: str | Path, out_dir: str | Path, data_dir: str | Path | None = None) -> ExportReport:
    """`data_dir` is needed when the index holds no evidence bytes, i.e. in file mode (issue #89)."""
    if not Path(db_path).is_file():
        raise ExportError(f"Datenbank nicht gefunden: {db_path}")

    conn = connect(db_path)
    try:
        files, report = _build(conn, Path(data_dir) if data_dir is not None else None)
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


def _build(conn, data_dir: Path | None = None) -> tuple[dict[str, bytes], ExportReport]:
    files: dict[str, bytes] = {}
    report = ExportReport()
    for topic in repository.list_topics(conn):
        document, evidence = _topic_document(conn, topic, report, data_dir=data_dir)
        files.update(evidence)
        files[f"{topic['key']}.yaml"] = _yaml_bytes(document)
        if len(files[f"{topic['key']}.yaml"]) > MAX_YAML_BYTES:
            report.too_large.append(f"{topic['key']}.yaml")
        report.topics += 1
    return files, report


def topic_document(
    conn, key: str, *, expand_refs: bool = False, evidence_ids: dict[str, int] | None = None,
    data_dir: Path | None = None, with_content: bool = True,
) -> tuple[dict, dict[str, bytes]] | None:
    """One topic in the import format, plus the evidence contents it refers to. Used for reports.

    With expand_refs, events from other topics that are linked to this one are written out in full
    instead of as keys. With evidence_ids, the database id of each evidence file is added to it, by its path in the document.
    Without with_content the evidence files are not read and the returned dict of contents is empty: the live
    dashboard serves them through the API and needs only their ids.
    """
    topic = repository.get_topic(conn, key)
    if topic is None:
        return None
    return _topic_document(conn, topic, ExportReport(), expand_refs=expand_refs, evidence_ids=evidence_ids,
                           data_dir=data_dir, with_content=with_content)


def _topic_document(
    conn, topic: dict, report: ExportReport, *, expand_refs: bool = False, evidence_ids: dict[str, int] | None = None,
    data_dir: Path | None = None, with_content: bool = True,
) -> tuple[dict, dict[str, bytes]]:
    files: dict[str, bytes] = {}
    events = []
    refs = []
    for event in repository.list_topic_events(conn, topic["key"]):
        if event["home_topic"] == topic["key"]:
            events.append(_event_document(conn, event, files, report, evidence_ids, data_dir, with_content))
        elif expand_refs:
            events.append(_event_document(conn, repository.get_event(conn, event["key"]), files, report, evidence_ids,
                                          data_dir, with_content))
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


def _event_document(
    conn, event: dict, files: dict[str, bytes], report: ExportReport, evidence_ids: dict[str, int] | None = None,
    data_dir: Path | None = None, with_content: bool = True,
) -> dict:
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
        relative = _evidence_path(event["key"], item["file"], used)
        if with_content:
            content = repository.get_evidence_content(conn, item["id"], data_dir)["content"]
            if content is None:
                raise ExportError(
                    f"Evidence {item['file']!r} von {event['key']!r} hat keinen gespeicherten Inhalt. "
                    "Bitte den Import erneut ausführen, im Dateimodus mit --data den Datenordner angeben."
                )
            files[relative] = content
        if evidence_ids is not None:
            evidence_ids[relative] = item["id"]
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
    parser.add_argument("--data", type=Path, default=None,
                        help="data folder with the evidence files, needed when the database holds no evidence bytes")
    args = parser.parse_args(argv)

    try:
        report = export_database(args.db, args.out, args.data)
    except ExportError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1

    print(f"Export nach {args.out}: {report.topics} Topics, {report.events} Events, {report.evidence} Evidence-Dateien")
    for name in report.too_large:
        print(f"Warnung: {name} ist größer als {MAX_YAML_BYTES // 2**20} MiB. Chronicle liest sie nicht wieder ein.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
