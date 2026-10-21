from pathlib import Path

import pytest
import yaml

from chronicle.builder import build_site
from chronicle.loader import TimelineError, collect_evidence, find_timeline, load_timeline
from chronicle.models import Timeline
from chronicle.renderer import render_dashboard

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def minimal_event(**overrides):
    event = {
        "t0": "2026-10-14T08:00:00+02:00",
        "phase": "Identification",
        "actor": "SIEM",
        "action": "Alarm",
        "status": "open",
    }
    event.update(overrides)
    return event


def test_sample_timeline_is_valid():
    timeline = load_timeline(find_timeline(DATA_DIR))
    assert timeline.events
    assert all(event.id for event in timeline.events)


def test_missing_ids_are_generated():
    timeline = Timeline.model_validate({"title": "T", "events": [minimal_event(), minimal_event()]})
    assert [event.id for event in timeline.events] == ["evt-001", "evt-002"]


def test_timestamps_are_normalized_to_utc():
    timeline = Timeline.model_validate({"title": "T", "events": [minimal_event()]})
    assert timeline.events[0].t0.utcoffset().total_seconds() == 0


def test_naive_timestamp_is_rejected():
    with pytest.raises(ValueError, match="timezone"):
        Timeline.model_validate({"title": "T", "events": [minimal_event(t0="2026-10-14T08:00:00")]})


def test_unknown_fields_are_rejected():
    with pytest.raises(ValueError):
        Timeline.model_validate({"title": "T", "events": [minimal_event(typo="x")]})


def test_unknown_trigger_reference_is_rejected():
    with pytest.raises(ValueError, match="triggered_by"):
        Timeline.model_validate({"title": "T", "events": [minimal_event(triggered_by="nobody")]})


def test_duplicate_ids_are_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        Timeline.model_validate({"title": "T", "events": [minimal_event(id="a"), minimal_event(id="a")]})


@pytest.mark.parametrize("path", ["../secret.txt", "/etc/passwd", "C:/Windows/win.ini"])
def test_unsafe_evidence_paths_are_rejected(path):
    event = minimal_event(evidence=[{"file": path, "type": "other"}])
    with pytest.raises(ValueError):
        Timeline.model_validate({"title": "T", "events": [event]})


def test_missing_evidence_file_is_reported(tmp_path):
    timeline = Timeline.model_validate(
        {"title": "T", "events": [minimal_event(evidence=[{"file": "nope.png", "type": "screenshot"}])]}
    )
    with pytest.raises(TimelineError, match="Datei fehlt"):
        collect_evidence(timeline, tmp_path)


def test_reserved_output_name_is_rejected(tmp_path):
    (tmp_path / "index.html").write_text("x")
    timeline = Timeline.model_validate(
        {"title": "T", "events": [minimal_event(evidence=[{"file": "index.html", "type": "other"}])]}
    )
    with pytest.raises(TimelineError, match="reserviert"):
        collect_evidence(timeline, tmp_path)


def test_multiple_timeline_files_are_rejected(tmp_path):
    for name in ("a", "b"):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "timeline.yaml").write_text(yaml.safe_dump({"title": "T", "events": [minimal_event()]}))
    with pytest.raises(TimelineError, match="Mehrere"):
        find_timeline(tmp_path)


def test_build_site_writes_index_and_evidence(tmp_path):
    data = tmp_path / "data"
    (data / "evidence").mkdir(parents=True)
    (data / "evidence" / "shot.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    (data / "timeline.yaml").write_text(
        yaml.safe_dump({"title": "Demo", "events": [minimal_event(evidence=[{"file": "evidence/shot.svg", "type": "screenshot"}])]})
    )

    index = build_site(data, tmp_path / "out")

    assert index.read_text(encoding="utf-8").count("Demo") >= 1
    assert (tmp_path / "out" / "evidence" / "shot.svg").is_file()


def test_rendered_html_embeds_payload_safely():
    timeline = Timeline.model_validate(
        {"title": "T", "events": [minimal_event(action="</script><script>alert(1)</script>")]}
    )
    html = render_dashboard(timeline)
    assert "</script><script>alert(1)" not in html
