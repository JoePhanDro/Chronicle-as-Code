"""After an edit in file mode the index is what a full import of the folder would give (ADR 0008, issue #125).

An edit of the fields updates one row and one file version. Anything else that changed in the folder makes the
index read the whole folder again, so a change made outside Chronicle is picked up with the next edit.
"""

from chronicle import repository
from chronicle.db import connect
from chronicle.importer import import_folder
from chronicle.models import Event

INDEX_QUERIES = {
    "events": "SELECT key, t0, phase, actor, system, action, status, triggered_by, details, home_topic "
              "FROM events ORDER BY key",
    "source_files": "SELECT path, sha256 FROM source_files ORDER BY path",
    "event_files": "SELECT event_key, path FROM event_files ORDER BY event_key",
    "topic_events": "SELECT topics.key, events.key FROM topic_events JOIN topics ON topics.pk = topic_events.topic_pk "
                    "JOIN events ON events.pk = topic_events.event_pk ORDER BY topics.key, events.key",
}


def index_of(conn) -> dict:
    return {name: [tuple(row) for row in conn.execute(query).fetchall()] for name, query in INDEX_QUERIES.items()}


def edit(conn, folder, key, **changes):
    current = repository.get_event(conn, key)
    data = {field: current[field] for field in ("key", "t0", "phase", "actor", "system", "action", "status",
                                                "triggered_by", "details")}
    data.update(changes)
    repository.update_event(conn, key, Event.model_validate(data), current["topics"], current["version"],
                            actor_id=None, ip="test", data_dir=folder)


def test_the_index_after_a_field_edit_equals_a_full_import(folder, conn, tmp_path):
    edit(conn, folder, "feed-cve", action="Nach der Änderung")
    edit(conn, folder, "feed-cve", action="Und noch einmal")

    fresh = tmp_path / "fresh.db"
    import_folder(fresh, folder)
    fresh_conn = connect(fresh)
    try:
        assert index_of(conn) == index_of(fresh_conn)
    finally:
        fresh_conn.close()


def test_an_edit_bumps_the_version_of_the_event_once(folder, conn):
    before = repository.get_event(conn, "feed-cve")["version"]

    edit(conn, folder, "feed-cve", action="Neu")

    assert repository.get_event(conn, "feed-cve")["version"] == before + 1


def test_a_change_made_outside_chronicle_is_indexed_with_the_next_edit(folder_with_b):
    b_file = folder_with_b / "b.yaml"
    b_file.write_text(b_file.read_text(encoding="utf-8").replace("Schritt B", "Außen geändert"), encoding="utf-8")
    conn = connect(folder_with_b.parent / "chronicle.db")
    try:
        edit(conn, folder_with_b, "feed-cve", action="Mit Änderung")

        assert repository.get_event(conn, "b1")["action"] == "Außen geändert"
    finally:
        conn.close()
