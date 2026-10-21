import sqlite3

import pytest

from chronicle import repository
from chronicle.db import connect
from chronicle.models import Timeline
from chronicle.repository import import_timeline


def event(key, **overrides):
    data = {
        "key": key,
        "t0": "2026-10-14T08:00:00+02:00",
        "phase": "Identification",
        "actor": "SIEM",
        "action": f"Aktion {key}",
        "status": "open",
    }
    data.update(overrides)
    return data


def topic_file(key, events=(), refs=(), title=None, kind="incident"):
    data = {"topic": {"key": key, "title": title or f"Topic {key}", "kind": kind}, "events": list(events)}
    if refs:
        data["refs"] = list(refs)
    return Timeline.model_validate(data)


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / "t.db")
    yield connection
    connection.close()


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_reimport_is_idempotent(conn):
    timeline = topic_file("a", [event("e1"), event("e2", triggered_by="e1")])
    import_timeline(conn, timeline)
    import_timeline(conn, timeline)
    assert count(conn, "topics") == 1
    assert count(conn, "events") == 2
    assert count(conn, "topic_events") == 2


def test_changed_event_is_updated_in_place(conn):
    import_timeline(conn, topic_file("a", [event("e1", action="alt")]))
    import_timeline(conn, topic_file("a", [event("e1", action="neu")]))
    assert count(conn, "events") == 1
    assert repository.get_event(conn, "e1")["action"] == "neu"


def test_topic_metadata_is_updated_in_place(conn):
    import_timeline(conn, topic_file("a", [], title="alt"))
    import_timeline(conn, topic_file("a", [], title="neu"))
    assert count(conn, "topics") == 1
    assert repository.get_topic(conn, "a")["title"] == "neu"


def test_evidence_is_replaced_not_appended(conn):
    with_evidence = event("e1", evidence=[{"file": "a.png", "type": "screenshot"}])
    import_timeline(conn, topic_file("a", [with_evidence]))
    import_timeline(conn, topic_file("a", [with_evidence]))
    assert count(conn, "evidence") == 1


def test_removed_event_stays_linked_and_kept(conn):
    # ADR 0003: an import only adds links. Removing a link is done in the UI.
    import_timeline(conn, topic_file("a", [event("e1"), event("e2")]))
    import_timeline(conn, topic_file("a", [event("e1")]))
    assert count(conn, "events") == 2
    assert [item["key"] for item in repository.list_topic_events(conn, "a")] == ["e1", "e2"]


def test_event_can_belong_to_several_topics_via_refs(conn):
    import_timeline(conn, topic_file("cve", [event("cve-hinweis")], kind="vulnerability"))
    import_timeline(conn, topic_file("incident-1", [event("e-exploit", triggered_by="cve-hinweis")], refs=["cve-hinweis"]))
    import_timeline(conn, topic_file("projekt-x", [], refs=["cve-hinweis"], kind="project"))

    assert [item["key"] for item in repository.list_topic_events(conn, "projekt-x")] == ["cve-hinweis"]
    assert repository.get_event(conn, "cve-hinweis")["topics"] == ["cve", "incident-1", "projekt-x"]
    assert repository.get_event(conn, "cve-hinweis")["home_topic"] == "cve"


def test_event_cannot_be_defined_in_a_second_topic(conn):
    import_timeline(conn, topic_file("a", [event("e1")]))
    with pytest.raises(ValueError, match="im Topic 'a' definiert"):
        import_timeline(conn, topic_file("b", [event("e1", action="kopie")]))
    assert repository.get_event(conn, "e1")["action"] == "Aktion e1"
    assert count(conn, "topics") == 1


def test_unknown_trigger_rolls_back_the_whole_import(conn):
    with pytest.raises(ValueError, match="triggered_by"):
        import_timeline(conn, topic_file("a", [event("e1"), event("e2", triggered_by="gibt-es-nicht")]))
    assert count(conn, "topics") == 0
    assert count(conn, "events") == 0


def test_unknown_ref_is_rejected(conn):
    with pytest.raises(ValueError, match="existiert nicht"):
        import_timeline(conn, topic_file("a", [], refs=["fehlt"]))
    assert count(conn, "topics") == 0


def test_trigger_may_point_to_an_event_from_another_topic(conn):
    import_timeline(conn, topic_file("a", [event("ursache")]))
    import_timeline(conn, topic_file("b", [event("folge", triggered_by="ursache")]))
    assert repository.get_event(conn, "folge")["triggered_by"] == "ursache"


LEGACY_SCHEMA = """
CREATE TABLE incidents (id INTEGER PRIMARY KEY, title TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00Z');
CREATE TABLE events (
    pk INTEGER PRIMARY KEY,
    incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    event_id TEXT NOT NULL, t0 TEXT NOT NULL, phase TEXT NOT NULL, actor TEXT NOT NULL, system TEXT,
    action TEXT NOT NULL, status TEXT NOT NULL, triggered_by TEXT, details TEXT NOT NULL DEFAULT '',
    UNIQUE (incident_id, event_id));
CREATE TABLE evidence (pk INTEGER PRIMARY KEY, event_pk INTEGER NOT NULL REFERENCES events(pk) ON DELETE CASCADE,
                       file TEXT NOT NULL, type TEXT NOT NULL, description TEXT NOT NULL DEFAULT '');
INSERT INTO incidents (id, title, summary) VALUES (1, 'Alt-Incident', 'Zusammenfassung');
INSERT INTO events (pk, incident_id, event_id, t0, phase, actor, action, status, triggered_by)
    VALUES (1, 1, 'first', '2026-10-14T06:00:00+00:00', 'Identification', 'SIEM', 'Erster', 'resolved', NULL),
           (2, 1, 'second', '2026-10-14T07:00:00+00:00', 'Analysis', 'Analyst', 'Zweiter', 'open', 'first');
INSERT INTO evidence (event_pk, file, type, description) VALUES (2, 'shot.png', 'screenshot', 'Beleg');
"""


def test_legacy_incidents_migrate_to_topics_once(tmp_path):
    path = tmp_path / "legacy.db"
    raw = sqlite3.connect(path)
    raw.executescript(LEGACY_SCHEMA)
    raw.commit()
    raw.close()

    conn = connect(path)
    topic = repository.get_topic(conn, "legacy-1")
    assert topic["title"] == "Alt-Incident"
    assert topic["kind"] == "incident"

    second = repository.get_event(conn, "legacy-1-second")
    assert second["triggered_by"] == "legacy-1-first"
    assert second["topics"] == ["legacy-1"]
    assert second["evidence"] == [{"file": "shot.png", "type": "screenshot", "description": "Beleg"}]
    assert count(conn, "events") == 2
    conn.close()

    # Second connect: nothing left to migrate, nothing duplicated.
    again = connect(path)
    assert count(again, "topics") == 1
    assert count(again, "events") == 2
    again.close()
