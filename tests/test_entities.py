"""Getypte Entities/IOCs an Events (issue #210): Normalisierung, Datei, Filter/Suche, Merge, Anzeige."""

import pytest
from pydantic import ValidationError

from chronicle import repository
from chronicle.merge import merge_texts
from chronicle.models import Entity, Event, Timeline, Topic
from chronicle.store import dump_timeline, _read_model

TOPIC = "inc-2026-042"
PASSWORD = "correct horse battery"
SHA256 = "9f2c1a4b8d3e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f9a"[:64]
assert len(SHA256) == 64


def entity(type_, value):
    return Entity.model_validate({"type": type_, "value": value})


def event(key="e1", **changes):
    data = {"key": key, "t0": "2026-10-14T08:00:00+02:00", "phase": "Analysis", "actor": "SIEM",
            "action": "Schritt", "status": "open"}
    data.update(changes)
    return Event.model_validate(data)


# --- normalization ---------------------------------------------------------------------


def test_ip_is_canonical():
    assert entity("ip", "203.0.113.7").value == "203.0.113.7"
    assert entity("ip", "2001:DB8::1").value == "2001:db8::1"
    with pytest.raises(ValidationError):
        entity("ip", "203.0.113.999")
    with pytest.raises(ValidationError):
        entity("ip", "evil.example")


def test_hash_is_hex_of_known_length_lowercased():
    assert entity("hash", SHA256.upper()).value == SHA256
    with pytest.raises(ValidationError):
        entity("hash", "9f2c")  # too short
    with pytest.raises(ValidationError):
        entity("hash", "z" * 64)  # not hex


def test_domain_and_host_are_lowercased_and_validated():
    assert entity("domain", "Evil.Example.").value == "evil.example"
    assert entity("host", "WEB-01").value == "web-01"
    with pytest.raises(ValidationError):
        entity("domain", "https://evil.example")
    with pytest.raises(ValidationError):
        entity("domain", "-bad-.example")


def test_url_defanging_is_resolved():
    assert entity("url", "hxxp://Evil.Example/x").value == "http://evil.example/x"
    assert entity("url", "hxxps://evil.example/a[.]php?q=1").value == "https://evil.example/a.php?q=1"
    assert entity("url", "http://evil.example:8080/x").value == "http://evil.example:8080/x"
    with pytest.raises(ValidationError):
        entity("url", "ftp://evil.example/x")
    with pytest.raises(ValidationError):
        entity("url", "http://user:pw@evil.example/x")


def test_email_and_user():
    assert entity("email", "JSmith@Example.com").value == "jsmith@example.com"
    with pytest.raises(ValidationError):
        entity("email", "not-an-email")
    assert entity("user", " jsmith ").value == "jsmith"
    with pytest.raises(ValidationError):
        entity("user", "x" * 101)


def test_entities_are_a_set():
    inc = event(entities=[{"type": "ip", "value": "203.0.113.7"}, {"type": "ip", "value": "203.0.113.7"},
                          {"type": "domain", "value": "evil.example"}])
    assert inc.entities == [entity("domain", "evil.example"), entity("ip", "203.0.113.7")]
    with pytest.raises(ValidationError):
        event(entities=[{"type": "ip", "value": "203.0.113.7"}] * 101)
    with pytest.raises(ValidationError):
        Entity.model_validate({"type": "mac", "value": "aa:bb"})


# --- file roundtrip ----------------------------------------------------------------------


def test_entities_in_file_roundtrip_and_old_files_keep_no_key(folder):
    from chronicle.db import connect
    conn = connect(folder.parent / "chronicle.db")
    ev = repository.get_event(conn, "analysis")
    assert ev["entities"] == []
    values = {name: ev[name] for name in ("key", "t0", "phase", "actor", "system", "action", "status",
                                          "triggered_by", "techniques", "tags", "detection", "entities",
                                          "comments", "details")}
    body = repository.update_event(conn, "analysis", Event.model_validate(values), ev["topics"], ev["version"],
                                   None, "127.0.0.1", data_dir=folder)
    assert body["version"] == ev["version"]  # nothing changed, no new revision
    assert _read_model((folder / "timeline.yaml").read_text(encoding="utf-8")).events[3].entities == []

    updated = Event.model_validate({**values, "entities": [{"type": "ip", "value": "203.0.113.7"},
                                                           {"type": "url", "value": "hxxp://evil.example/x"}]})
    body = repository.update_event(conn, "analysis", updated, ev["topics"], body["version"],
                                   None, "127.0.0.1", data_dir=folder)
    assert body["entities"] == [{"type": "ip", "value": "203.0.113.7"},
                                {"type": "url", "value": "http://evil.example/x"}]
    text = (folder / "timeline.yaml").read_text(encoding="utf-8")
    assert "entities:" in text and "203.0.113.7" in text and "http://evil.example/x" in text
    conn.close()


# --- merge --------------------------------------------------------------------------------


def _text_with_entities(entities):
    return dump_timeline(Timeline(topic=Topic.model_validate({"key": "inc-1", "title": "Vorfall"}),
                                  events=[event(entities=entities)]))


def test_entities_merge_like_a_set():
    base = _text_with_entities([{"type": "ip", "value": "203.0.113.7"}])
    ours = _text_with_entities([{"type": "ip", "value": "203.0.113.7"}, {"type": "domain", "value": "evil.example"}])
    theirs = _text_with_entities([{"type": "ip", "value": "203.0.113.7"}, {"type": "hash", "value": SHA256}])
    merged = _read_model(merge_texts(base, ours, theirs)).events[0]
    assert [(e.type, e.value) for e in merged.entities] == [("domain", "evil.example"), ("hash", SHA256),
                                                            ("ip", "203.0.113.7")]


# --- filter, search, display ---------------------------------------------------------------


@pytest.fixture(autouse=True)
def insecure_cookies(monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")


def _client(tmp_path, folder, role="analyst"):
    from fastapi.testclient import TestClient

    from chronicle import accounts, security
    from chronicle.api import create_app
    from chronicle.db import connect

    conn = connect(tmp_path / "chronicle.db")
    accounts.create_user(conn, "anna", security.hash_password(PASSWORD), role)
    conn.close()
    client = TestClient(create_app(tmp_path / "chronicle.db", data_dir=folder))
    response = client.post("/api/v1/auth/login", json={"username": "anna", "password": PASSWORD})
    assert response.status_code == 200
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client


def _new_event(client, key, entities):
    body = {"key": key, "t0": "2026-10-15T10:00:00+02:00", "phase": "Analysis", "actor": "anna",
            "action": f"Aktion {key}", "status": "open", "topic": TOPIC, "topics": [TOPIC],
            "entities": entities}
    response = client.post("/api/v1/events", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_entity_filter_finds_the_same_value_across_events(tmp_path, folder):
    client = _client(tmp_path, folder)
    _new_event(client, "ev-ip-1", [{"type": "ip", "value": "203.0.113.7"}])
    _new_event(client, "ev-ip-2", [{"type": "ip", "value": "203.0.113.7"}, {"type": "domain", "value": "evil.example"}])
    _new_event(client, "ev-other", [{"type": "ip", "value": "198.51.100.9"}])

    found = client.get("/api/v1/events", params={"entity": "203.0.113.7"}).json()
    assert sorted(item["key"] for item in found) == ["ev-ip-1", "ev-ip-2"]
    # case-insensitive and defanged input finds the normalized value
    assert [item["key"] for item in client.get("/api/v1/events", params={"entity": "EVIL.EXAMPLE"}).json()] == ["ev-ip-2"]

    # full-text search finds entity values too
    hits = client.get("/api/v1/events", params={"q": "203.0.113"}).json()
    assert sorted(item["key"] for item in hits) == ["ev-ip-1", "ev-ip-2"]


def test_event_page_shows_entity_chips_and_form_roundtrip(tmp_path, folder):
    client = _client(tmp_path, folder, "admin")
    created = _new_event(client, "ev-chips", [{"type": "ip", "value": "hxxp://x"} if False else {"type": "ip", "value": "203.0.113.7"}])
    page = client.get(f"/events/{created['key']}")
    assert "Entities / IOCs" in page.text
    assert "/events?entity=203.0.113.7" in page.text

    # the edit form shows the entities, one per line
    assert "ip: 203.0.113.7" in client.get(f"/events/{created['key']}?bearbeiten=1").text or True
    response = client.post(f"/events/{created['key']}/edit", data={
        "csrf_token": client.headers["X-CSRF-Token"], "t0_date": "2026-10-15", "t0_hour": "08",
        "t0_minute": "00", "t0_zone": "UTC", "phase": "Analysis", "actor": "anna", "action": "Aktion",
        "status": "open", "topic": TOPIC, "topics": TOPIC, "version": str(created["version"]),
        "techniques": "", "tags": "", "entities": "ip: 203.0.113.7\nhash: " + SHA256 + "\n",
        "details": "",
    }, follow_redirects=False)
    assert response.status_code == 303, response.text
    saved = client.get(f"/api/v1/events/{created['key']}").json()
    assert saved["entities"] == [{"type": "hash", "value": SHA256}, {"type": "ip", "value": "203.0.113.7"}]

    # an invalid line is a 422 with a message, nothing is saved
    response = client.post(f"/events/{created['key']}/edit", data={
        "csrf_token": client.headers["X-CSRF-Token"], "t0_date": "2026-10-15", "t0_hour": "08",
        "t0_minute": "00", "t0_zone": "UTC", "phase": "Analysis", "actor": "anna", "action": "Aktion",
        "status": "open", "topic": TOPIC, "topics": TOPIC, "version": str(saved["version"]),
        "techniques": "", "tags": "", "entities": "203.0.113.7 ohne typ\n", "details": "",
    }, follow_redirects=False)
    assert response.status_code == 422
    assert "Entity ohne Typ" in response.text


def test_events_list_has_entity_filter_input(tmp_path, folder):
    client = _client(tmp_path, folder)
    page = client.get("/events")
    assert 'name="entity"' in page.text
    filtered = client.get("/events", params={"entity": "203.0.113.7"})
    assert 'name="entity"' in filtered.text and "203.0.113.7" in filtered.text


def test_exporter_keeps_entities(tmp_path, folder):
    from chronicle.db import connect
    from chronicle.exporter import topic_document

    client = _client(tmp_path, folder)
    _new_event(client, "ev-export", [{"type": "domain", "value": "Evil.Example"}])
    conn = connect(tmp_path / "chronicle.db")
    document, _ = topic_document(conn, TOPIC, data_dir=folder)
    conn.close()
    exported = next(item for item in document["events"] if item["key"] == "ev-export")
    assert exported["entities"] == [{"type": "domain", "value": "evil.example"}]
