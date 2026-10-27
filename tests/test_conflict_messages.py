"""A topic file changed outside Chronicle: every write names the same cause and what did not happen (issue #124)."""

import pytest

from chronicle import repository
from chronicle.db import connect
from chronicle.errors import APPLY_HINT, changed_outside, not_in_a_file
from chronicle.evidence import check_file
from chronicle.models import Event, Topic
from chronicle.repository import Conflict

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def new_event(key="neu-1"):
    return Event.model_validate({
        "key": key, "t0": "2026-10-15T10:00:00+02:00", "phase": "Analysis", "actor": "A", "action": "Neu",
        "status": "open",
    })


def touch(path):
    path.write_text(path.read_text(encoding="utf-8") + "\n# von Hand angehängt\n", encoding="utf-8")


def evidence_id(conn, event_key):
    return conn.execute(
        "SELECT evidence.pk FROM evidence JOIN events ON events.pk = evidence.event_pk WHERE events.key = ?", (event_key,)
    ).fetchone()["pk"]


def test_the_helpers_keep_the_wording_of_the_messages():
    assert APPLY_HINT == "Bitte chronicle apply ausführen."
    assert str(changed_outside("a.yaml", "Nichts wurde gelöscht")) == (
        "a.yaml wurde außerhalb von Chronicle geändert. Bitte chronicle apply ausführen. Nichts wurde gelöscht."
    )
    assert str(changed_outside("a.yaml", "Das Event wurde nicht angelegt", "Grund")) == (
        "a.yaml wurde außerhalb von Chronicle geändert. Bitte chronicle apply ausführen. "
        "Das Event wurde nicht angelegt (Grund)."
    )
    assert not_in_a_file("Event 'x'") == "Event 'x' steht in keiner Datei des Datenordners. Bitte chronicle apply ausführen."


def call(folder, conn, operation):
    current = repository.get_event(conn, "analysis")
    if operation == "update_event":
        event = Event.model_validate({name: current[name] for name in ("key", "t0", "phase", "actor", "system", "action",
                                                                      "status", "triggered_by", "details")}
                                     | {"action": "Geändert"})
        return lambda: repository.update_event(conn, "analysis", event, current["topics"], current["version"],
                                               actor_id=None, ip="test", data_dir=folder)
    return {
        "create_event": lambda: repository.create_event(conn, new_event(), ["inc-2026-042"], None, "test", folder),
        "delete_event": lambda: repository.delete_event(conn, "lessons", None, "test", folder),
        "add_evidence": lambda: repository.add_evidence(conn, "lessons", check_file("a.png", PNG), "screenshot", "", None,
                                                        "test", folder),
        "delete_evidence": lambda: repository.delete_evidence(conn, evidence_id(conn, "siem-alert"), None, "test", folder),
        "update_topic": lambda: repository.update_topic(
            conn, "inc-2026-042", Topic(key="inc-2026-042", title="Neuer Titel", kind="incident"), None, "test", folder),
    }[operation]


@pytest.mark.parametrize("operation, outcome", [
    ("update_event", "Die Änderung wurde nicht gespeichert ("),
    ("create_event", "Das Event wurde nicht angelegt ("),
    ("delete_event", "Nichts wurde gelöscht ("),
    ("add_evidence", "Der Beleg wurde nicht angehängt ("),
    ("delete_evidence", "Der Beleg wurde nicht entfernt ("),
    ("update_topic", "Die Änderung wurde nicht gespeichert ("),
])
def test_a_file_changed_outside_is_a_conflict_that_says_what_did_not_happen(folder, conn, operation, outcome):
    action = call(folder, conn, operation)
    touch(folder / "timeline.yaml")

    with pytest.raises(Conflict) as excinfo:
        action()

    assert str(excinfo.value).startswith(f"timeline.yaml wurde außerhalb von Chronicle geändert. {APPLY_HINT} {outcome}")


def test_deleting_a_topic_names_the_same_cause(folder, conn):
    touch(folder / "timeline.yaml")
    with pytest.raises(Conflict) as excinfo:
        repository.delete_topic(conn, "inc-2026-042", None, "test", folder)
    assert str(excinfo.value) == (
        f"timeline.yaml wurde außerhalb von Chronicle geändert. {APPLY_HINT} Nichts wurde gelöscht."
    )


def test_a_link_into_a_file_changed_outside_names_the_same_cause(folder_with_b):
    conn = connect(folder_with_b.parent / "chronicle.db")
    touch(folder_with_b / "b.yaml")
    with pytest.raises(Conflict) as excinfo:
        repository.link_event(conn, "b", "analysis", None, "test", folder_with_b)
    conn.close()
    assert str(excinfo.value).startswith(
        f"b.yaml wurde außerhalb von Chronicle geändert. {APPLY_HINT} Der Verweis wurde nicht geändert ("
    )


def test_an_event_the_index_knows_but_no_file_holds_says_so(folder, conn):
    conn.execute("DELETE FROM event_files WHERE event_key = 'lessons'")
    conn.commit()
    with pytest.raises(repository.Invalid) as excinfo:
        repository.delete_event(conn, "lessons", None, "test", folder)
    assert str(excinfo.value) == "Event 'lessons' steht in keiner Datei des Datenordners. Bitte chronicle apply ausführen."
