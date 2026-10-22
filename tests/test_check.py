"""chronicle check: the rules run on the files alone, without database or server (ADR 0009, issue #92)."""

from pathlib import Path

import pytest

from chronicle.check import check_folder, cycle_problems, load_folder
from chronicle.cli import main
from chronicle.importer import import_folder
from chronicle.loader import CheckFailed

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def event(key, triggered_by=None, evidence=None):
    lines = [
        f"  - key: {key}",
        "    t0: 2026-10-14T08:40:00+02:00",
        "    phase: Exploit",
        "    actor: Angreifer",
        f"    action: Schritt {key}",
        "    status: open",
    ]
    if isinstance(triggered_by, list):
        lines += ["    triggered_by:"] + [f"      - {cause}" for cause in triggered_by]
    elif triggered_by:
        lines.append(f"    triggered_by: {triggered_by}")
    if evidence:
        lines += ["    evidence:", f"      - file: {evidence}", "        type: screenshot"]
    return "\n".join(lines)


def write_topic(folder, name, topic_key, events=(), refs=()):
    body = f"topic:\n  key: {topic_key}\n  title: Titel {topic_key}\n"
    if events:
        body += "events:\n" + "\n".join(events) + "\n"
    if refs:
        body += "refs:\n" + "".join(f"  - {ref}\n" for ref in refs)
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def snapshot(folder):
    return {path.relative_to(folder).as_posix(): path.read_bytes() for path in folder.rglob("*") if path.is_file()}


def test_demo_data_passes():
    assert check_folder(DATA_DIR) == []


def test_valid_folder_passes(tmp_path):
    write_topic(tmp_path, "a.yaml", "a", events=[event("a-1")])
    write_topic(tmp_path, "b.yaml", "b", events=[event("b-1", triggered_by="a-1")], refs=["a-1"])
    assert check_folder(tmp_path) == []


def test_duplicate_event_key_across_files_is_reported(tmp_path):
    write_topic(tmp_path, "a.yaml", "a", events=[event("doppelt")])
    write_topic(tmp_path, "b.yaml", "b", events=[event("doppelt")])
    (problem,) = check_folder(tmp_path)
    assert "'doppelt'" in problem and "a.yaml" in problem and "b.yaml" in problem


def test_duplicate_topic_key_across_files_is_reported(tmp_path):
    write_topic(tmp_path, "a.yaml", "gleich", events=[event("a-1")])
    write_topic(tmp_path, "b.yaml", "gleich", events=[event("b-1")])
    (problem,) = check_folder(tmp_path)
    assert "Topic 'gleich'" in problem


def test_unknown_refs_and_triggers_are_reported(tmp_path):
    write_topic(tmp_path, "a.yaml", "a", events=[event("a-1", triggered_by="gibt-es-nicht")], refs=["auch-nicht"])
    problems = check_folder(tmp_path)
    assert any("'auch-nicht'" in problem for problem in problems)
    assert any("'gibt-es-nicht'" in problem for problem in problems)


def test_self_trigger_is_reported(tmp_path):
    write_topic(tmp_path, "a.yaml", "a", events=[event("a-1", triggered_by="a-1")])
    (problem,) = check_folder(tmp_path)
    assert "selbst auslösen" in problem


def test_cycle_across_files_is_reported(tmp_path):
    write_topic(tmp_path, "a.yaml", "a", events=[event("a-1", triggered_by="b-1")], refs=["b-1"])
    write_topic(tmp_path, "b.yaml", "b", events=[event("b-1", triggered_by="a-1")], refs=["a-1"])
    (problem,) = check_folder(tmp_path)
    assert "Kreis" in problem and "a-1" in problem and "b-1" in problem


def test_several_causes_without_a_cycle_pass(tmp_path):
    write_topic(tmp_path, "a.yaml", "a", events=[event("a-1"), event("a-2")])
    write_topic(tmp_path, "b.yaml", "b", events=[event("b-1", triggered_by=["a-1", "a-2"])], refs=["a-1", "a-2"])
    assert check_folder(tmp_path) == []


def test_a_cycle_through_the_second_cause_is_reported(tmp_path):
    write_topic(tmp_path, "a.yaml", "a", events=[event("a-1", triggered_by=["b-1"])], refs=["b-1"])
    write_topic(tmp_path, "b.yaml", "b", events=[event("b-1", triggered_by=["a-9", "a-1"])], refs=["a-1"])
    assert any("Kreis" in problem for problem in check_folder(tmp_path))


def test_cycle_problems_finds_each_cycle_once():
    assert cycle_problems({"a": "b", "b": "c", "c": "a"}) == ["Auslösungen bilden einen Kreis: a → b → c → a"]
    assert cycle_problems({"a": "b", "b": "c"}) == []


def test_missing_evidence_is_reported_unless_skipped(tmp_path):
    write_topic(tmp_path, "a.yaml", "a", events=[event("a-1", evidence="evidence/fehlt.png")])
    (problem,) = check_folder(tmp_path)
    assert "fehlt" in problem
    assert check_folder(tmp_path, evidence=False) == []


def test_evidence_path_is_relative_to_the_data_folder(tmp_path):
    (tmp_path / "evidence").mkdir()
    (tmp_path / "evidence" / "shot.png").write_bytes(PNG)
    write_topic(tmp_path / "topics", "a.yaml", "a", events=[event("a-1", evidence="evidence/shot.png")])
    assert check_folder(tmp_path) == []


def test_problems_of_all_files_are_reported(tmp_path):
    (tmp_path / "x.yaml").write_text("topic:\n  key: x\n", encoding="utf-8")  # title is missing
    (tmp_path / "y.yaml").write_text("topic:\n  key: y\n", encoding="utf-8")
    problems = check_folder(tmp_path)
    assert len(problems) == 2


def test_load_folder_raises_with_every_problem(tmp_path):
    write_topic(tmp_path, "a.yaml", "gleich", events=[event("x")])
    write_topic(tmp_path, "b.yaml", "gleich", events=[event("x")])
    with pytest.raises(CheckFailed) as caught:
        load_folder(tmp_path)
    assert len(caught.value.problems) == 2  # topic key and event key


def test_check_writes_nothing(tmp_path, capsys):
    folder = tmp_path / "data"
    write_topic(folder, "a.yaml", "a", events=[event("a-1")])
    before = snapshot(folder)

    assert main(["check", str(folder)]) == 0

    assert snapshot(folder) == before
    assert not list(tmp_path.glob("*.db"))
    assert "OK" in capsys.readouterr().out


def test_cli_returns_one_and_lists_problems(tmp_path, capsys):
    write_topic(tmp_path, "a.yaml", "a", events=[event("a-1", triggered_by="a-1")])
    assert main(["check", str(tmp_path)]) == 1
    assert "selbst auslösen" in capsys.readouterr().err


def test_cli_skips_evidence_with_flag(tmp_path):
    write_topic(tmp_path, "a.yaml", "a", events=[event("a-1", evidence="evidence/fehlt.png")])
    assert main(["check", str(tmp_path)]) == 1
    assert main(["check", str(tmp_path), "--ohne-anhaenge"]) == 0


def test_import_rejects_duplicate_topic_keys(tmp_path):
    folder = tmp_path / "data"
    write_topic(folder, "a.yaml", "gleich", events=[event("a-1")])
    write_topic(folder, "b.yaml", "gleich", events=[event("b-1")])
    with pytest.raises(CheckFailed):
        import_folder(tmp_path / "chronicle.db", folder)


def test_import_resolves_triggers_into_events_already_in_the_database(tmp_path):
    first = tmp_path / "first"
    write_topic(first, "a.yaml", "a", events=[event("a-1")])
    import_folder(tmp_path / "chronicle.db", first)

    second = tmp_path / "second"
    write_topic(second, "b.yaml", "b", events=[event("b-1", triggered_by="a-1")])
    (report,) = import_folder(tmp_path / "chronicle.db", second)
    assert report.topic == "b"
