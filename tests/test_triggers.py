"""triggered_by holds any number of causes (issue #87). Cycles are refused in files and in the UI alike."""

import pytest

from chronicle import repository
from chronicle.db import connect
from chronicle.models import Event
from chronicle.repository import Conflict, Invalid
from chronicle.check import cycle_problems


def event_model(key, triggered_by=None):
    data = {
        "key": key, "t0": "2026-10-14T08:00:00+02:00", "phase": "Analysis",
        "actor": "X", "action": f"Schritt {key}", "status": "open",
    }
    if triggered_by is not None:
        data["triggered_by"] = triggered_by
    return Event.model_validate(data)


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / "triggers.db")
    yield connection
    connection.close()


def add(conn, key, triggered_by=None):
    # actor_id None: these checks do not depend on who acts. Only the audit entry records the user.
    return repository.create_event(conn, event_model(key, triggered_by), ["inc"], actor_id=None, ip="test")


@pytest.fixture
def topic(conn):
    conn.execute("INSERT INTO topics (key, title, kind) VALUES ('inc', 'Vorfall', 'incident')")
    conn.commit()


def test_an_event_can_have_several_causes(conn, topic):
    add(conn, "a")
    add(conn, "b")
    stored = add(conn, "c", ["b", "a", "a"])
    assert stored["triggered_by"] == ["a", "b"]  # sorted and without repeats


def test_no_cause_is_an_empty_list(conn, topic):
    assert add(conn, "a")["triggered_by"] == []


def test_a_cause_that_does_not_exist_is_refused(conn, topic):
    with pytest.raises(Invalid, match="gibt-es-nicht"):
        add(conn, "a", ["gibt-es-nicht"])


def test_a_cycle_through_the_ui_is_refused(conn, topic):
    add(conn, "a")
    add(conn, "b", ["a"])
    expected = conn.execute("SELECT version FROM events WHERE key = 'a'").fetchone()["version"]
    with pytest.raises(Invalid, match="Kreis"):
        repository.update_event(conn, "a", event_model("a", ["b"]), ["inc"], expected, actor_id=None, ip="test")


def test_a_cycle_through_a_second_cause_is_refused(conn, topic):
    add(conn, "a")
    add(conn, "b")
    add(conn, "c", ["a", "b"])
    expected = conn.execute("SELECT version FROM events WHERE key = 'b'").fetchone()["version"]
    with pytest.raises(Invalid, match="Kreis"):
        repository.update_event(conn, "b", event_model("b", ["c"]), ["inc"], expected, actor_id=None, ip="test")


def test_an_event_cannot_be_deleted_while_another_one_depends_on_it(conn, topic):
    add(conn, "a")
    add(conn, "b")
    add(conn, "c", ["a", "b"])
    with pytest.raises(Conflict, match="von c"):
        repository.delete_event(conn, "a", actor_id=None, ip="test")


def test_the_migrated_single_value_reads_as_a_list(tmp_path):
    conn = connect(tmp_path / "legacy.db")
    conn.execute("INSERT INTO topics (key, title, kind) VALUES ('inc', 'Vorfall', 'incident')")
    conn.execute(
        "INSERT INTO events (key, t0, phase, actor, action, status, triggered_by, home_topic) "
        "VALUES ('b', '2026-10-14T08:00:00Z', 'Analysis', 'X', 'b', 'open', 'a', 'inc')"
    )
    conn.commit()
    conn.close()

    conn = connect(tmp_path / "legacy.db")  # a second start must not change the value again
    assert repository.get_event(conn, "b")["triggered_by"] == ["a"]
    conn.close()


def test_cycle_problems_follow_every_cause():
    assert cycle_problems({"a": ["b", "c"], "c": ["d"], "d": []}) == []
    assert cycle_problems({"a": ["b", "c"], "b": [], "c": ["a"]}) == ["Auslösungen bilden einen Kreis: a → c → a"]
