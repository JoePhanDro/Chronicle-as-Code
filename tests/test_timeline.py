from pathlib import Path

import pytest
import yaml

from chronicle.builder import build_site
from chronicle.loader import TimelineError, collect_evidence, find_timeline, load_timeline
from chronicle.models import Timeline
from chronicle.renderer import render_dashboard

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TOPIC = {"key": "t-1", "title": "Test", "kind": "incident"}


def minimal_event(**overrides):
    event = {
        "key": "ev-1",
        "t0": "2026-10-14T08:00:00+02:00",
        "phase": "Identification",
        "actor": "SIEM",
        "action": "Alarm",
        "status": "open",
    }
    event.update(overrides)
    return event


def timeline(events=None, refs=None, topic=None):
    data = {"topic": topic or TOPIC, "events": events if events is not None else [minimal_event()]}
    if refs is not None:
        data["refs"] = refs
    return Timeline.model_validate(data)


def test_sample_timeline_is_valid():
    loaded = load_timeline(find_timeline(DATA_DIR))
    assert loaded.topic.key == "inc-2026-042"
    assert len(loaded.events) == 7
    assert all(event.key for event in loaded.events)


def test_event_key_is_required():
    event = minimal_event()
    del event["key"]
    with pytest.raises(ValueError):
        timeline(events=[event])


def test_old_id_field_is_rejected():
    with pytest.raises(ValueError):
        timeline(events=[minimal_event(id="x")])


def test_timestamps_are_normalized_to_utc():
    assert timeline().events[0].t0.utcoffset().total_seconds() == 0


def test_naive_timestamp_is_rejected():
    with pytest.raises(ValueError, match="timezone"):
        timeline(events=[minimal_event(t0="2026-10-14T08:00:00")])


def test_unknown_fields_are_rejected():
    with pytest.raises(ValueError):
        timeline(events=[minimal_event(typo="x")])


def test_duplicate_keys_in_one_file_are_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        timeline(events=[minimal_event(), minimal_event()])


def test_key_cannot_be_both_defined_and_referenced():
    with pytest.raises(ValueError, match="cannot also be referenced"):
        timeline(refs=["ev-1"])


def test_topic_kind_must_be_known():
    with pytest.raises(ValueError):
        timeline(topic={"key": "t-1", "title": "T", "kind": "unbekannt"})


def test_topic_without_events_is_allowed_when_it_only_references():
    result = timeline(events=[], refs=["other-event"])
    assert result.events == []


@pytest.mark.parametrize("path", ["../secret.txt", "/etc/passwd", "C:/Windows/win.ini"])
def test_unsafe_evidence_paths_are_rejected(path):
    with pytest.raises(ValueError):
        timeline(events=[minimal_event(evidence=[{"file": path, "type": "other"}])])


def test_missing_evidence_file_is_reported(tmp_path):
    result = timeline(events=[minimal_event(evidence=[{"file": "nope.png", "type": "screenshot"}])])
    with pytest.raises(TimelineError, match="Datei fehlt"):
        collect_evidence(result, tmp_path)


def test_reserved_output_name_is_rejected(tmp_path):
    (tmp_path / "index.html").write_text("x")
    result = timeline(events=[minimal_event(evidence=[{"file": "index.html", "type": "other"}])])
    with pytest.raises(TimelineError, match="reserviert"):
        collect_evidence(result, tmp_path)


def test_multiple_timeline_files_are_rejected(tmp_path):
    for name in ("a", "b"):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "timeline.yaml").write_text(yaml.safe_dump({"topic": TOPIC, "events": [minimal_event()]}))
    with pytest.raises(TimelineError, match="Mehrere"):
        find_timeline(tmp_path)


def test_build_site_writes_index_and_evidence(tmp_path):
    data = tmp_path / "data"
    (data / "evidence").mkdir(parents=True)
    (data / "evidence" / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    (data / "timeline.yaml").write_text(
        yaml.safe_dump(
            {
                "topic": TOPIC,
                "events": [minimal_event(evidence=[{"file": "evidence/shot.png", "type": "screenshot"}])],
            }
        )
    )

    index = build_site(data, tmp_path / "out")

    assert "Test" in index.read_text(encoding="utf-8")
    assert "Berichts-Snapshot" in index.read_text(encoding="utf-8")
    assert (tmp_path / "out" / "evidence" / "shot.png").is_file()


def test_build_site_rejects_svg_evidence(tmp_path):
    data = tmp_path / "data"
    (data / "evidence").mkdir(parents=True)
    (data / "evidence" / "shot.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    (data / "timeline.yaml").write_text(
        yaml.safe_dump(
            {
                "topic": TOPIC,
                "events": [minimal_event(evidence=[{"file": "evidence/shot.svg", "type": "screenshot"}])],
            }
        )
    )

    with pytest.raises(TimelineError, match="shot.svg"):
        build_site(data, tmp_path / "out")


def test_rendered_html_embeds_payload_safely():
    result = timeline(events=[minimal_event(action="</script><script>alert(1)</script>")])
    html = render_dashboard(result)
    assert "</script><script>alert(1)" not in html
