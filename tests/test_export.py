import shutil
import stat
from pathlib import Path

import pytest
import yaml

from chronicle import accounts, repository, security
from chronicle.backup import BackupError, backup_database, main as backup_main
from chronicle.db import connect
from chronicle.evidence import check_file
from chronicle.exporter import ExportError, export_database, main as export_main
from chronicle.importer import import_folder
from chronicle.loader import CheckFailed
from chronicle.models import Event

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PASSWORD = "correct horse battery"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32

PROJECT = """
topic:
  key: projekt-x
  title: "Projekt X: Härtung"
  kind: project
  summary: "Mehrzeilige\\nZusammenfassung"
events:
  - key: proj-1
    t0: 2026-10-20T09:00:00+02:00
    phase: Lessons Learned
    actor: Projektleitung
    action: "Härtung geplant"
    status: open
    triggered_by: exploit
    details: |
      Erster Absatz mit **Markdown**.

      - Punkt eins
      - Punkt zwei
refs:
  - mitigation
"""


def tree(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


@pytest.fixture
def source_db(tmp_path):
    """A database with the sample topic, a second topic, a manual event and an uploaded file."""
    path = tmp_path / "source.db"
    conn = connect(path)
    user_id = accounts.create_user(conn, "anna", security.hash_password(PASSWORD), "analyst")
    conn.close()

    data = tmp_path / "data"  # the files are the source of truth (ADR 0011)
    shutil.copytree(DATA_DIR, data)
    (data / "projekt.yaml").write_text(PROJECT, encoding="utf-8")
    import_folder(path, data)

    conn = connect(path)
    repository.create_event(
        conn,
        Event.model_validate({
            "key": "manuell-1", "t0": "2026-10-15T10:00:00+02:00", "phase": "Analysis", "actor": "Analyst",
            "action": "Manuell angelegt", "status": "open", "details": "Text mit `Code` und \"Anführungszeichen\"",
        }),
        ["inc-2026-042", "projekt-x"],
        user_id,
        "127.0.0.1",
        data,
    )
    repository.add_evidence(
        conn, "exploit", check_file("probe.png", PNG), "screenshot", "Probe", user_id, "127.0.0.1", data
    )
    conn.close()
    return path


def export(db, out):
    """The export of a database whose evidence lives in the data folder next to it."""
    return export_database(db, out, Path(db).parent / "data")


def test_export_then_import_into_an_empty_database_gives_the_same_export(source_db, tmp_path):
    first = tmp_path / "export-1"
    report = export(source_db, first)
    assert (report.topics, report.events) == (2, 9)
    assert report.evidence == 3  # two sample files and the upload

    fresh = tmp_path / "fresh.db"
    import_folder(fresh, first)

    second = tmp_path / "export-2"
    export_database(fresh, second)
    assert tree(first) == tree(second)


def test_roundtrip_keeps_cross_topic_links_and_evidence(source_db, tmp_path):
    export(source_db, tmp_path / "export")
    fresh = tmp_path / "fresh.db"
    import_folder(fresh, tmp_path / "export")

    conn = connect(fresh)
    assert sorted(repository.get_event(conn, "manuell-1")["topics"]) == ["inc-2026-042", "projekt-x"]
    assert repository.get_event(conn, "proj-1")["triggered_by"] == ["exploit"]
    assert "projekt-x" in repository.get_event(conn, "mitigation")["topics"]
    evidence = repository.get_event(conn, "exploit")["evidence"]
    assert [(item["file"], item["mime"]) for item in evidence] == [("evidence/exploit/probe.png", "image/png")]
    conn.close()


def test_export_writes_one_yaml_per_topic_in_the_import_format(source_db, tmp_path):
    export(source_db, tmp_path / "export")

    document = yaml.safe_load((tmp_path / "export" / "projekt-x.yaml").read_text(encoding="utf-8"))
    assert document["topic"]["title"] == "Projekt X: Härtung"
    assert document["topic"]["summary"] == "Mehrzeilige\nZusammenfassung"
    assert [event["key"] for event in document["events"]] == ["proj-1"]
    assert document["refs"] == ["manuell-1", "mitigation"]


def test_multiline_details_are_written_as_literal_blocks(source_db, tmp_path):
    export(source_db, tmp_path / "export")
    text = (tmp_path / "export" / "projekt-x.yaml").read_text(encoding="utf-8")
    assert "details: |" in text
    assert "Erster Absatz mit **Markdown**." in text


def test_export_fails_without_writing_when_evidence_has_no_content(source_db, tmp_path):
    conn = connect(source_db)
    conn.execute("UPDATE evidence SET content = NULL, mime = NULL")
    conn.commit()
    conn.close()

    out = tmp_path / "export"
    with pytest.raises(ExportError, match="Import erneut"):
        export_database(source_db, out)
    assert not out.exists()


def test_export_of_a_missing_database_is_an_error(tmp_path):
    with pytest.raises(ExportError, match="nicht gefunden"):
        export_database(tmp_path / "gibt-es-nicht.db", tmp_path / "out")


def test_export_cli_reports_the_counts(source_db, tmp_path, capsys):
    assert export_main(["--db", str(source_db), "--out", str(tmp_path / "out"), "--data", str(tmp_path / "data")]) == 0
    assert "2 Topics, 9 Events, 3 Evidence-Dateien" in capsys.readouterr().out


def test_topic_files_may_refer_to_each_other_without_a_cycle(tmp_path):
    # a1 is defined in a.yaml and referenced from b.yaml via refs; b1 is defined in b.yaml and triggered by a1.
    # Cycles in triggered_by are rejected (docs/notation.md, section 5c), so a1 must not be triggered by b1 here.
    folder = tmp_path / "data"
    folder.mkdir()
    (folder / "a.yaml").write_text(
        yaml.safe_dump({"topic": {"key": "a", "title": "A"},
                        "events": [{"key": "a1", "t0": "2026-10-14T08:00:00+02:00", "phase": "Analysis",
                                    "actor": "X", "action": "a1", "status": "open"}]}),
        encoding="utf-8",
    )
    (folder / "b.yaml").write_text(
        yaml.safe_dump({"topic": {"key": "b", "title": "B"},
                        "events": [{"key": "b1", "t0": "2026-10-14T09:00:00+02:00", "phase": "Analysis",
                                    "actor": "X", "action": "b1", "status": "open", "triggered_by": "a1"}],
                        "refs": ["a1"]}),
        encoding="utf-8",
    )
    db = tmp_path / "links.db"

    reports = import_folder(db, folder)

    assert sorted(report.topic for report in reports) == ["a", "b"]
    conn = connect(db)
    assert repository.get_event(conn, "b1")["triggered_by"] == ["a1"]
    conn.close()


def test_the_same_event_key_in_two_topic_files_is_rejected(tmp_path):
    folder = tmp_path / "data"
    folder.mkdir()
    for topic in ("a", "b"):
        (folder / f"{topic}.yaml").write_text(
            yaml.safe_dump({"topic": {"key": topic, "title": topic},
                            "events": [{"key": "dup", "t0": "2026-10-14T08:00:00+02:00", "phase": "Analysis",
                                        "actor": "X", "action": "x", "status": "open"}]}),
            encoding="utf-8",
        )
    db = tmp_path / "dup.db"

    with pytest.raises(CheckFailed, match="dup"):
        import_folder(db, folder)
    conn = connect(db)
    assert repository.list_topics(conn) == []
    conn.close()


# Backup

def test_backup_is_a_complete_owner_only_copy(source_db, tmp_path):
    target = tmp_path / "backup" / "chronicle.db"
    backup_database(source_db, target)

    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    conn = connect(target)
    assert len(repository.list_events(conn)) == 9
    assert accounts.get_user_by_name(conn, "anna") is not None
    conn.close()


def test_backup_never_overwrites_an_existing_file(source_db, tmp_path):
    target = tmp_path / "chronicle.db"
    target.write_bytes(b"alt")
    with pytest.raises(BackupError, match="existiert bereits"):
        backup_database(source_db, target)
    assert target.read_bytes() == b"alt"


def test_backup_of_a_missing_database_is_an_error(tmp_path):
    with pytest.raises(BackupError, match="nicht gefunden"):
        backup_database(tmp_path / "gibt-es-nicht.db", tmp_path / "out.db")


def test_backup_cli_writes_a_timestamped_file_into_a_folder(source_db, tmp_path, capsys):
    folder = tmp_path / "backups"
    folder.mkdir()
    assert backup_main(["--db", str(source_db), "--out", str(folder)]) == 0
    written = sorted(path for path in folder.glob("chronicle-*.db") if not path.name.endswith(".ops.db"))
    assert len(written) == 1  # the index copy; the operations copy sits beside it as chronicle-*.ops.db
    assert len(list(folder.glob("chronicle-*.ops.db"))) == 1
    assert str(written[0]) in capsys.readouterr().out


def test_a_topic_file_over_the_size_limit_is_reported(source_db, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("chronicle.exporter.MAX_YAML_BYTES", 3000)  # the sample topic is bigger, the project topic is not

    code = export_main(["--db", str(source_db), "--out", str(tmp_path / "export"), "--data", str(tmp_path / "data")])

    assert code == 0
    assert (tmp_path / "export" / "inc-2026-042.yaml").exists()  # the export is written all the same
    assert "Warnung: inc-2026-042.yaml ist größer als" in capsys.readouterr().err
