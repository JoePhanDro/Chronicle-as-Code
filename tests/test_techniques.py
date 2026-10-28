"""MITRE ATT&CK techniques on an event (ADR 0026, issue #209)."""

import pytest
from pydantic import ValidationError

from chronicle.merge import merge_texts
from chronicle.models import Event, Timeline, Topic
from chronicle.store import _read_model, dump_timeline


@pytest.fixture(autouse=True)
def insecure_cookies_for_http_tests(monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")


def event(key="e1", **changes):
    data = {"key": key, "t0": "2026-10-14T08:00:00+02:00", "phase": "Analysis", "actor": "SIEM",
            "action": "Schritt", "status": "open"}
    data.update(changes)
    return Event.model_validate(data)


def text(*events):
    return dump_timeline(Timeline(topic=Topic(key="inc-1", title="Vorfall", kind="incident"), events=list(events)))


# --- the model -------------------------------------------------------------------------------------------------------

def test_techniques_are_tidied_sorted_and_unique():
    assert event(techniques=[" t1059.001", "T1190", "T1190"]).techniques == ["T1059.001", "T1190"]


@pytest.mark.parametrize("value", ["T119", "T11900", "1190", "T1190.1", "T1190.0011", "TA0001", "T1190 T1059", ""])
def test_an_id_in_the_wrong_format_is_refused(value):
    with pytest.raises(ValidationError, match="ATT&CK"):
        event(techniques=[value])


def test_nothing_or_an_empty_string_means_no_technique():
    assert event().techniques == [] and event(techniques="").techniques == [] and event(techniques=None).techniques == []


def test_more_than_fifty_are_refused():
    with pytest.raises(ValidationError):
        event(techniques=[f"T{number:04d}" for number in range(51)])


# --- the file --------------------------------------------------------------------------------------------------------

def test_the_field_is_written_only_when_it_is_set_and_reads_back():
    plain = text(event())
    assert "techniques" not in plain  # old files stay as they are

    with_techniques = text(event(techniques=["T1190", "T1059.001"]))

    assert "techniques:" in with_techniques
    assert _read_model(with_techniques).events[0].techniques == ["T1059.001", "T1190"]


# --- the merge -------------------------------------------------------------------------------------------------------

def merged(base, ours, theirs):
    return _read_model(merge_texts(text(base), text(ours), text(theirs))).events[0].techniques


def test_techniques_added_on_both_sides_are_all_kept_without_a_conflict():
    assert merged(event(), event(techniques=["T1190"]), event(techniques=["T1059"])) == ["T1059", "T1190"]


def test_a_technique_removed_on_one_side_is_removed():
    base = event(techniques=["T1059", "T1190"])
    assert merged(base, event(techniques=["T1059", "T1190", "T1566"]), event(techniques=["T1190"])) == ["T1190", "T1566"]


def test_techniques_do_not_hide_a_conflict_in_another_field():
    from chronicle.merge import MergeConflict

    base = event()
    with pytest.raises(MergeConflict):
        merge_texts(text(base), text(event(action="A", techniques=["T1190"])), text(event(action="B")))


# --- the app ---------------------------------------------------------------------------------------------------------

def body_from(current, **changes):
    body = {field: current[field] for field in ("key", "t0", "phase", "actor", "system", "action", "status",
                                                "triggered_by", "techniques", "details", "version")}
    body.update(topic=current["home_topic"], topics=current["topics"], **changes)
    return body


def test_the_api_stores_and_returns_techniques_and_the_file_holds_them(api_client, folder):
    client = api_client("analyst")
    current = client.get("/api/v1/events/analysis").json()
    assert current["techniques"] == []

    response = client.put("/api/v1/events/analysis", json=body_from(current, techniques=["t1190", "T1059.001"]))

    assert response.status_code == 200, response.text
    assert response.json()["techniques"] == ["T1059.001", "T1190"]
    assert "T1059.001" in (folder / "timeline.yaml").read_text(encoding="utf-8")
    assert client.get("/api/v1/events/analysis").json()["techniques"] == ["T1059.001", "T1190"]


def test_a_bad_id_over_the_api_is_a_422(api_client):
    client = api_client("analyst")
    current = client.get("/api/v1/events/analysis").json()

    response = client.put("/api/v1/events/analysis", json=body_from(current, techniques=["XYZ"]))

    assert response.status_code == 422


def test_the_edit_form_shows_techniques_and_saving_keeps_them(api_client):
    client = api_client("analyst")
    current = client.get("/api/v1/events/analysis").json()
    client.put("/api/v1/events/analysis", json=body_from(current, techniques=["T1190", "T1059.001"]))

    page = client.get("/events/analysis").text
    assert 'name="techniques" value="T1059.001, T1190"' in page
    assert 'href="https://attack.mitre.org/techniques/T1059/001/"' in page

    form = {"csrf_token": client.headers["X-CSRF-Token"], "key": "analysis", "phase": current["phase"], "status": current["status"],
            "actor": current["actor"], "system": current["system"] or "", "action": "Neu", "details": current["details"],
            "t0_date": current["t0"][:10], "t0_hour": current["t0"][11:13], "t0_minute": current["t0"][14:16],
            "t0_zone": "UTC", "topic": current["home_topic"], "topics": current["topics"],
            "version": str(client.get("/api/v1/events/analysis").json()["version"]),
            "techniques": "t1190; T1566"}
    response = client.post("/events/analysis/edit", data=form)

    assert response.status_code in (200, 303), response.text
    assert client.get("/api/v1/events/analysis").json()["techniques"] == ["T1190", "T1566"]


def test_a_bad_id_in_the_form_is_shown_and_nothing_is_saved(api_client):
    client = api_client("analyst")
    current = client.get("/api/v1/events/analysis").json()
    form = {"csrf_token": client.headers["X-CSRF-Token"], "key": "analysis", "phase": current["phase"], "status": current["status"],
            "actor": current["actor"], "system": "", "action": current["action"], "details": "",
            "t0_date": current["t0"][:10], "t0_hour": current["t0"][11:13], "t0_minute": current["t0"][14:16],
            "t0_zone": "UTC", "topic": current["home_topic"], "topics": current["topics"],
            "version": str(current["version"]), "techniques": "T12"}

    response = client.post("/events/analysis/edit", data=form)

    assert response.status_code == 422 and "ATT&CK" in response.text.replace("&amp;", "&")
    assert client.get("/api/v1/events/analysis").json()["techniques"] == []


def test_changing_the_status_chip_keeps_the_techniques(api_client):
    client = api_client("analyst")
    current = client.get("/api/v1/events/analysis").json()
    client.put("/api/v1/events/analysis", json=body_from(current, techniques=["T1190"]))
    current = client.get("/api/v1/events/analysis").json()

    client.post("/events/analysis/mark", data={"csrf_token": client.headers["X-CSRF-Token"],
                                                  "phase": current["phase"], "status": "resolved",
                                                  "version": str(current["version"])})

    after = client.get("/api/v1/events/analysis").json()
    assert after["status"] == "resolved" and after["techniques"] == ["T1190"]


def test_closing_all_events_of_a_topic_keeps_their_techniques(api_client):
    client = api_client("analyst")
    current = client.get("/api/v1/events/analysis").json()
    client.put("/api/v1/events/analysis", json=body_from(current, techniques=["T1505.003"], status="open"))

    client.post("/topics/inc-2026-042/close-events", data={"csrf_token": client.headers["X-CSRF-Token"]})

    after = client.get("/api/v1/events/analysis").json()
    assert after["status"] == "resolved" and after["techniques"] == ["T1505.003"]
