"""IOC export: the typed entities of one topic or of all topics as CSV or as a STIX 2.1 bundle (issue #259, ADR 0030).

Entities are collected from the index, deduplicated by (type, value); each carries first/last seen (the t0 range
of the events that hold it) and its sources (event keys, topics). The audit log records the export with format,
scope and count only — never the IOC values themselves.
"""

from __future__ import annotations

import csv
import io
import ipaddress
import json
import sqlite3
import uuid
from datetime import date

from . import accounts

FORMATS = ("csv", "stix")
STIX_SPEC_VERSION = "2.1"

# STIX 2.1 indicator types and patterns per entity type (ADR 0030). hash maps by length, ip by version.
HASH_ALGORITHM = {32: "md5", 40: "sha1", 64: "sha256", 128: "sha512"}


def _stix_indicator_type(entity_type: str, value: str) -> str:
    if entity_type == "ip":
        return "ipv6-addr" if ipaddress.ip_address(value).version == 6 else "ipv4-addr"
    if entity_type == "hash":
        return "file"  # the pattern names the hash algorithm
    if entity_type in ("domain", "host"):
        return "domain-name"
    if entity_type == "url":
        return "url"
    if entity_type == "email":
        return "email-addr"
    return "user-account"


def _stix_pattern(entity_type: str, value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    if entity_type == "ip":
        kind = "ipv6-addr" if ipaddress.ip_address(value).version == 6 else "ipv4-addr"
        return f"[{kind}:value = '{escaped}']"
    if entity_type == "hash":
        algorithm = HASH_ALGORITHM[len(value)]
        return f"[file:hashes.{algorithm} = '{escaped}']"
    if entity_type in ("domain", "host"):
        return f"[domain-name:value = '{escaped}']"
    if entity_type == "url":
        return f"[url:value = '{escaped}']"
    if entity_type == "email":
        return f"[email-addr:value = '{escaped}']"
    return f"[user-account:display_name = '{escaped}']"


def collect_entities(
    conn: sqlite3.Connection,
    topic_key: str | None = None,
    since: date | None = None,
    until: date | None = None,
) -> list[dict]:
    """All entities of the events (of one topic when `topic_key` is set), deduplicated by (type, value), each with
    first_seen, last_seen, the event keys that hold it (events) and their topics. `since`/`until` bound the t0
    day in UTC. Sorted by type, then value."""
    sql = "SELECT events.pk, events.key, events.t0, events.entities FROM events"
    conditions, params = [], []
    if topic_key is not None:
        conditions.append(
            "events.pk IN (SELECT topic_events.event_pk FROM topic_events "
            "JOIN topics ON topics.pk = topic_events.topic_pk WHERE topics.key = ?)"
        )
        params.append(topic_key)
    if since is not None:
        conditions.append("substr(events.t0, 1, 10) >= ?")
        params.append(since.isoformat())
    if until is not None:
        conditions.append("substr(events.t0, 1, 10) <= ?")
        params.append(until.isoformat())
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY events.t0, events.pk"
    rows = conn.execute(sql, params).fetchall()

    topic_keys_of: dict[int, list[str]] = {}
    if rows:
        marks = ",".join("?" * len(rows))
        pks = [row["pk"] for row in rows]
        for link in conn.execute(
            f"SELECT topic_events.event_pk, topics.key FROM topic_events "
            f"JOIN topics ON topics.pk = topic_events.topic_pk WHERE topic_events.event_pk IN ({marks})",
            pks,
        ).fetchall():
            topic_keys_of.setdefault(link["event_pk"], []).append(link["key"])

    collected: dict[tuple[str, str], dict] = {}
    for row in rows:
        if not row["entities"]:
            continue
        for item in json.loads(row["entities"]):
            pair = (item["type"], item["value"])
            entry = collected.setdefault(pair, {
                "type": item["type"], "value": item["value"],
                "first_seen": row["t0"], "last_seen": row["t0"], "events": [], "topics": set(),
            })
            entry["first_seen"] = min(entry["first_seen"], row["t0"])
            entry["last_seen"] = max(entry["last_seen"], row["t0"])
            entry["events"].append(row["key"])
            entry["topics"].update(topic_keys_of.get(row["pk"], []))
    result = []
    for entry in collected.values():
        entry["events"] = sorted(entry["events"])
        entry["topics"] = sorted(entry["topics"])
        result.append(entry)
    return sorted(result, key=lambda item: (item["type"], item["value"]))


# Leading characters that make Excel/LibreOffice interpret a cell as a formula (CSV injection).
CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_cell(value: str) -> str:
    """Neutralize formula injection: a cell starting with = + - @ tab or CR gets a leading apostrophe,
    so spreadsheet apps show the text instead of running it (GitHub Advanced Security on PR #271)."""
    if value and value[0] in CSV_FORMULA_PREFIXES:
        return "'" + value
    return value


def entities_csv(rows: list[dict]) -> bytes:
    """CSV with a header row: type, value, first_seen, last_seen, events and topics (';'-separated in the cell).
    Every text cell is passed through _csv_cell."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(["type", "value", "first_seen", "last_seen", "events", "topics"])
    for row in rows:
        writer.writerow([_csv_cell(row["type"]), _csv_cell(row["value"]), _csv_cell(row["first_seen"]),
                         _csv_cell(row["last_seen"]), _csv_cell(";".join(row["events"])),
                         _csv_cell(";".join(row["topics"]))])
    return buffer.getvalue().encode("utf-8")


def entities_stix(rows: list[dict]) -> bytes:
    """A STIX 2.1 bundle with one indicator per entity (ADR 0030). IDs are deterministic (uuid5 over type+value),
    so re-exports of the same entity produce the same indicator id."""
    objects = []
    for row in rows:
        objects.append({
            "type": "indicator",
            "spec_version": STIX_SPEC_VERSION,
            "id": f"indicator--{uuid.uuid5(uuid.NAMESPACE_URL, f'chronicle:ioc:{row['type']}:{row['value']}')}",
            "created": row["first_seen"],
            "modified": row["last_seen"],
            "valid_from": row["first_seen"],
            "pattern": _stix_pattern(row["type"], row["value"]),
            "pattern_type": "stix",
            "indicator_types": [_stix_indicator_type(row["type"], row["value"])],
            "x_chronicle_first_seen": row["first_seen"],
            "x_chronicle_last_seen": row["last_seen"],
            "x_chronicle_events": row["events"],
            "x_chronicle_topics": row["topics"],
        })
    bundle = {"type": "bundle", "id": f"bundle--{uuid.uuid4()}", "objects": objects}
    return json.dumps(bundle, ensure_ascii=False, indent=2).encode("utf-8")


def export_headers(filename: str) -> dict[str, str]:
    return {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
    }


def make_ioc_export(
    conn: sqlite3.Connection,
    topic_key: str | None,
    format: str,
    since: date | None,
    until: date | None,
    actor_id: int | None,
    ip: str | None,
) -> tuple[str, bytes, str]:
    """Build the export and write the audit entry. Returns (filename, body, media type)."""
    rows = collect_entities(conn, topic_key, since, until)
    scope = topic_key or "all"
    if format == "csv":
        filename, body, media = f"iocs-{scope}.csv", entities_csv(rows), "text/csv; charset=utf-8"
    else:
        filename, body, media = f"iocs-{scope}.stix.json", entities_stix(rows), "application/stix+json"
    accounts.audit(conn, "ioc_export", user_id=actor_id, target=scope,
                   detail=f"format={format} count={len(rows)}", ip=ip)
    return filename, body, media
