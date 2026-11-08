"""Zeiträume (t1) und Entdeckungszeit (detected_at) an Events (issue #208, ADR 0025)."""

import itertools
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from chronicle import repository
from chronicle.merge import merge_texts
from chronicle.models import Event, Timeline, Topic, human_span
from chronicle.store import dump_timeline, _read_model

TOPIC = "inc-2026-042"
PASSWORD = "correct horse battery"


def event(key="e1", **changes):
    data = {"key": key, "t0": "2026-10-14T08:00:00+02:00", "phase": "Analysis", "actor": "SIEM",
            "action": "Schritt", "status": "open"}
    data.update(changes)
    return Event.model_validate(data)


# --- model -----------------------------------------------------------------------------


def test_t1_must_not_lie_before_t0_and_needs_a_timezone():
    assert event(t1="2026-10-14T09:00:00+02:00").t1 is not None
    assert event(t1="2026-10-14T06:00:00+00:00").t1 == event().t0  # equality allowed, same instant
    with pytest.raises(ValidationError, match="t1"):
        event(t1="2026-10-13T08:00:00+02:00")
    with pytest.raises(ValidationError):
        event(t1="2026-10-15T08:00:00")  # no timezone
    with pytest.raises(ValidationError):
        event(detected_at="2026-10-15T08:00:00")  # no timezone


def test_detected_at_may_lie_before_t0():
    early = event(detected_at="2026-10-10T08:00:00+02:00")
    assert early.detected_at < early.t0  # allowed: estimates and clock skew are real


def test_times_are_stored_as_utc():
    ev = event(t1="2026-10-14T10:00:00+02:00", detected_at="2026-10-15T10:00:00+02:00")
    assert ev.t1.isoformat() == "2026-10-14T08:00:00+00:00"
    assert ev.detected_at.isoformat() == "2026-10-15T08:00:00+00:00"


def test_human_span():
    assert human_span(0) == "0 s"
    assert human_span(42) == "42 s"
    assert human_span(42 * 60) == "42 min"
    assert human_span(5 * 3600 + 3 * 60) == "5 h 3 min"
    assert human_span(3 * 86400 + 2 * 3600) == "3 d 2 h"
    assert human_span(-90) == "1 min"  # sign is the caller's business


# --- file: old files stay valid and unchanged -------------------------------------------


def test_old_files_stay_byte_identical(folder):
    from chronicle.db import connect
    path = folder / "timeline.yaml"
    before = path.read_bytes()
    conn = connect(folder.parent / "chronicle.db")
    current = repository.get_event(conn, "analysis")
    data = {name: current[name] for name in ("key", "t0", "phase", "actor", "system", "action", "status",
                                             "triggered_by", "techniques", "tags", "detection", "entities",
                                             "comments", "details")}
    body = repository.update_event(conn, "analysis", Event.model_validate(data), current["topics"],
                                   current["version"], None, "127.0.0.1", data_dir=folder)
    conn.close()
    assert body["version"] == current["version"]  # nothing changed
    assert path.read_bytes() == before
    assert "t1" not in path.read_text(encoding="utf-8") and "detected_at" not in path.read_text(encoding="utf-8")


def test_t1_and_detected_at_roundtrip_through_the_file(folder):
    from chronicle.db import connect
    conn = connect(folder.parent / "chronicle.db")
    current = repository.get_event(conn, "analysis")
    data = {name: current[name] for name in ("key", "t0", "phase", "actor", "system", "action", "status",
                                             "triggered_by", "techniques", "tags", "detection", "entities",
                                             "comments", "details")}
    updated = Event.model_validate({**data, "t1": "2026-10-16T12:00:00+02:00",
                                   "detected_at": "2026-10-20T09:30:00+02:00"})
    body = repository.update_event(conn, "analysis", updated, current["topics"], current["version"],
                                   None, "127.0.0.1", data_dir=folder)
    assert body["t1"] == "2026-10-16T10:00:00+00:00"
    assert body["detected_at"] == "2026-10-20T07:30:00+00:00"
    written = _read_model((folder / "timeline.yaml").read_text(encoding="utf-8"))
    written_event = next(item for item in written.events if item.key == "analysis")
    assert written_event.t1 == datetime(2026, 10, 16, 10, 0, tzinfo=UTC)
    assert written_event.detected_at == datetime(2026, 10, 20, 7, 30, tzinfo=UTC)
    # removing them again removes the keys
    body2 = repository.update_event(conn, "analysis", Event.model_validate(data), current["topics"], body["version"],
                                    None, "127.0.0.1", data_dir=folder)
    assert body2["t1"] is None and body2["detected_at"] is None
    written = _read_model((folder / "timeline.yaml").read_text(encoding="utf-8"))
    written_event = next(item for item in written.events if item.key == "analysis")
    assert written_event.t1 is None and written_event.detected_at is None
    conn.close()


def test_t1_before_t0_is_rejected_on_write(folder):
    from chronicle.db import connect
    conn = connect(folder.parent / "chronicle.db")
    current = repository.get_event(conn, "analysis")
    data = {name: current[name] for name in ("key", "t0", "phase", "actor", "system", "action", "status",
                                             "triggered_by", "techniques", "tags", "detection", "entities",
                                             "comments", "details")}
    with pytest.raises(ValidationError):
        Event.model_validate({**data, "t1": "2026-10-01T00:00:00+02:00"})
    conn.close()


# --- merge -------------------------------------------------------------------------------


def _text_with_times(**changes):
    return dump_timeline(Timeline(topic=Topic.model_validate({"key": "inc-1", "title": "Vorfall"}),
                                  events=[event(**changes)]))


def test_different_new_time_fields_merge_cleanly():
    base = _text_with_times()
    ours = _text_with_times(t1="2026-10-15T08:00:00+02:00")
    theirs = _text_with_times(detected_at="2026-10-20T08:00:00+02:00")
    merged = _read_model(merge_texts(base, ours, theirs)).events[0]
    assert merged.t1 is not None and merged.detected_at is not None


def test_merge_result_violating_t1_t0_is_a_conflict():
    from chronicle.merge import MergeConflict
    base = _text_with_times()
    ours = _text_with_times(t1="2026-10-15T08:00:00+02:00")
    theirs = _text_with_times(t0="2026-10-16T08:00:00+02:00")  # t0 moved after the merged t1
    with pytest.raises(MergeConflict, match="t1"):
        merge_texts(base, ours, theirs)


# --- display, timeline, report -------------------------------------------------------------


@pytest.fixture(autouse=True)
def insecure_cookies(monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")


def _client(tmp_path, folder, role="analyst"):
    from fastapi.testclient import TestClient

    from chronicle import accounts, security
    from chronicle.api import create_app
    from chronicle.db import connect

    name = f"user{next(_client.counter)}"
    conn = connect(tmp_path / "chronicle.db")
    accounts.create_user(conn, name, security.hash_password(PASSWORD), role)
    conn.close()
    client = TestClient(create_app(tmp_path / "chronicle.db", data_dir=folder))
    response = client.post("/api/v1/auth/login", json={"username": name, "password": PASSWORD})
    assert response.status_code == 200
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client


_client.counter = itertools.count()


def _new_event(client, key, **extra):
    body = {"key": key, "t0": "2026-10-15T10:00:00+02:00", "phase": "Analysis", "actor": "anna",
            "action": f"Aktion {key}", "status": "open", "topic": TOPIC, "topics": [TOPIC]}
    body.update(extra)
    response = client.post("/api/v1/events", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_api_returns_the_new_fields(tmp_path, folder):
    client = _client(tmp_path, folder)
    created = _new_event(client, "ev-range", t1="2026-10-17T10:00:00+02:00", detected_at="2026-10-20T10:00:00+02:00")
    assert created["t1"] == "2026-10-17T08:00:00+00:00"
    assert created["detected_at"] == "2026-10-20T08:00:00+00:00"
    plain = _new_event(client, "ev-point")
    assert plain["t1"] is None and plain["detected_at"] is None
    # t1 < t0 is refused by the API
    response = client.post("/api/v1/events", json={**{k: plain[k] for k in ("t0", "phase", "actor", "action", "status")},
                                                   "key": "ev-bad", "topic": TOPIC, "topics": [TOPIC],
                                                   "t1": "2026-10-01T00:00:00+02:00"})
    assert response.status_code == 422


def test_event_page_and_list_show_range_and_dwell(tmp_path, folder):
    client = _client(tmp_path, folder)
    created = _new_event(client, "ev-range", t1="2026-10-18T10:00:00+02:00", detected_at="2026-10-14T10:00:00+02:00")
    page = client.get(f"/events/{created['key']}")
    assert "Ende (t1)" in page.text and "Dauer" in page.text and "3 d" in page.text
    assert "Verweildauer" in page.text
    assert "Entdeckung liegt vor t0" in page.text  # detected before t0: hint, not an error
    listing = client.get("/events")
    assert "–" in listing.text  # the range in the list row


def test_form_roundtrip(tmp_path, folder):
    client = _client(tmp_path, folder, "admin")
    created = _new_event(client, "ev-form")
    response = client.post(f"/events/{created['key']}/edit", data={
        "csrf_token": client.headers["X-CSRF-Token"],
        "t0_date": "2026-10-15", "t0_hour": "08", "t0_minute": "00", "t0_zone": "UTC",
        "t1_date": "2026-10-17", "t1_hour": "08", "t1_minute": "30", "t1_zone": "UTC",
        "detected_date": "2026-10-20", "detected_hour": "09", "detected_minute": "00", "detected_zone": "UTC",
        "phase": "Analysis", "actor": "anna", "action": "Aktion", "status": "open",
        "topic": TOPIC, "topics": TOPIC, "version": str(created["version"]),
        "techniques": "", "tags": "", "entities": "", "details": "",
    }, follow_redirects=False)
    assert response.status_code == 303, response.text
    saved = client.get(f"/api/v1/events/{created['key']}").json()
    assert saved["t1"] == "2026-10-17T08:30:00+00:00"
    assert saved["detected_at"] == "2026-10-20T09:00:00+00:00"


def test_timeline_payload_has_end_for_ranges(tmp_path, folder):
    from chronicle.db import connect
    from chronicle.renderer import _event_payload

    client = _client(tmp_path, folder)
    _new_event(client, "ev-range", t1="2026-10-17T10:00:00+02:00")
    _new_event(client, "ev-point")
    conn = connect(tmp_path / "chronicle.db")
    ranged = repository.get_event(conn, "ev-range")
    point = repository.get_event(conn, "ev-point")
    conn.close()
    ranged_model = Event.model_validate({name: ranged[name] for name in Event.model_fields})
    point_model = Event.model_validate({name: point[name] for name in Event.model_fields})
    assert _event_payload(ranged_model, lambda item: "") ["t1"] == "2026-10-17T08:00:00+00:00"
    assert _event_payload(point_model, lambda item: "")["t1"] is None


def test_pdf_shows_range_and_dwell(tmp_path, folder):
    from chronicle.db import connect
    from chronicle.exporter import topic_document
    from chronicle.pdf_report import _event_block

    client = _client(tmp_path, folder)
    _new_event(client, "ev-range", t1="2026-10-18T10:00:00+02:00", detected_at="2026-10-20T10:00:00+02:00")
    conn = connect(tmp_path / "chronicle.db")
    document, _ = topic_document(conn, TOPIC, expand_refs=True, data_dir=folder)
    conn.close()
    timeline = Timeline.model_validate(document)
    ranged = next(e for e in timeline.events if e.key == "ev-range")
    text = " ".join(item.getPlainText() for item in _event_block(ranged, timeline.events, {}) if hasattr(item, "getPlainText"))
    assert "bis" in text and "3 d" in text
    assert "entdeckt" in text and "Verweildauer" in text and "5 d" in text
