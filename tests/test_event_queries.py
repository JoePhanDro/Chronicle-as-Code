from pathlib import Path

import yaml

from chronicle import repository
from chronicle.db import connect
from chronicle.importer import import_folder

EVENT_COUNT = 1000
WITH_EVIDENCE = range(0, EVENT_COUNT, 10)  # every tenth event has one piece of evidence


def write_timeline(folder: Path) -> None:
    (folder / "evidence").mkdir(parents=True)
    (folder / "evidence" / "protokoll.txt").write_text("Protokollauszug\n", encoding="utf-8")
    events = []
    for index in range(EVENT_COUNT):
        event = {
            "key": f"ev-{index:04d}",
            "t0": f"2026-10-14T{index // 60 % 24:02d}:{index % 60:02d}:00+02:00",
            "phase": "Identification",
            "actor": "SIEM",
            "action": f"Alarm {index}",
            "status": "open",
        }
        if index in WITH_EVIDENCE:
            event["evidence"] = [{"file": "evidence/protokoll.txt", "type": "log", "description": f"Beleg {index}"}]
        events.append(event)
    topic = {"key": "last-test", "title": "Lasttest", "kind": "incident"}
    (folder / "timeline.yaml").write_text(yaml.safe_dump({"topic": topic, "events": events}, allow_unicode=True), encoding="utf-8")


def test_listing_many_events_takes_a_fixed_number_of_queries(tmp_path):
    folder = tmp_path / "data"
    write_timeline(folder)
    db_path = tmp_path / "queries.db"
    import_folder(db_path, folder)

    conn = connect(db_path)
    statements = []
    conn.set_trace_callback(statements.append)
    events = repository.list_events(conn)
    conn.set_trace_callback(None)
    conn.close()

    assert len(events) == EVENT_COUNT
    assert len(statements) <= 5, f"{len(statements)} queries for {EVENT_COUNT} events"


def test_evidence_is_attached_to_the_right_events(tmp_path):
    folder = tmp_path / "data"
    write_timeline(folder)
    db_path = tmp_path / "evidence.db"
    import_folder(db_path, folder)

    conn = connect(db_path)
    events = {event["key"]: event for event in repository.list_events(conn)}
    conn.close()

    for index in range(EVENT_COUNT):
        event = events[f"ev-{index:04d}"]
        if index in WITH_EVIDENCE:
            assert [item["description"] for item in event["evidence"]] == [f"Beleg {index}"]
        else:
            assert event["evidence"] == []
