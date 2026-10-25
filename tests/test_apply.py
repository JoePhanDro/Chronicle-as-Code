"""chronicle apply writes the files into the index; the faster YAML loader reads the same data (issue #96)."""

from pathlib import Path

import yaml

from chronicle import yamlscan
from chronicle.cli import main

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def test_apply_writes_the_index_and_is_idempotent(tmp_path, capsys):
    db = tmp_path / "chronicle.db"
    assert main(["apply", str(DATA_DIR), "--db", str(db)]) == 0
    first = capsys.readouterr().out
    assert "angelegt: 7" in first

    assert main(["apply", str(DATA_DIR), "--db", str(db)]) == 0
    second = capsys.readouterr().out
    assert "angelegt: 0, aktualisiert: 0" in second  # nothing new, the second run changes nothing


def test_a_failed_check_leaves_no_database_behind(tmp_path, capsys):
    folder = tmp_path / "data"
    folder.mkdir()
    (folder / "a.yaml").write_text("topic:\n  key: a\n", encoding="utf-8")  # title is missing
    db = tmp_path / "chronicle.db"

    assert main(["apply", str(folder), "--db", str(db)]) == 1
    assert not db.exists()
    assert "Fehler" in capsys.readouterr().err


def test_the_fast_loader_reads_the_same_data_as_the_safe_loader():
    text = (DATA_DIR / "timeline.yaml").read_text(encoding="utf-8")
    assert yaml.load(text, Loader=yamlscan.YAML_LOADER) == yaml.load(text, Loader=yaml.SafeLoader)


def test_unchanged_reimport_keeps_versions_and_only_a_real_change_bumps(tmp_path):
    import sqlite3
    from chronicle.importer import import_folder

    folder = tmp_path / "data"
    folder.mkdir()
    (folder / "a.yaml").write_text(
        "topic:\n  key: a\n  title: A\nevents:\n"
        "  - key: a1\n    t0: 2026-10-14T08:00:00+02:00\n    phase: Exploit\n    actor: X\n    action: eins\n    status: open\n"
        "  - key: a2\n    t0: 2026-10-14T09:00:00+02:00\n    phase: Exploit\n    actor: X\n    action: zwei\n    status: open\n",
        encoding="utf-8",
    )
    db = tmp_path / "chronicle.db"

    def versions():
        conn = sqlite3.connect(db)
        try:
            return dict(conn.execute("SELECT key, version FROM events"))
        finally:
            conn.close()

    import_folder(db, folder)
    assert versions() == {"a1": 1, "a2": 1}
    (report,) = import_folder(db, folder)
    assert versions() == {"a1": 1, "a2": 1}  # nothing changed, so no version moved
    assert report.updated == []

    (folder / "a.yaml").write_text((folder / "a.yaml").read_text(encoding="utf-8").replace("eins", "eins, korrigiert"), encoding="utf-8")
    (report,) = import_folder(db, folder)
    assert versions() == {"a1": 2, "a2": 1}  # only the edited event moved
    assert report.updated == ["a1"]


def test_changed_evidence_bumps_the_version_of_its_event(tmp_path):
    import sqlite3
    from chronicle.importer import import_folder

    folder = tmp_path / "data"
    (folder / "evidence").mkdir(parents=True)
    (folder / "evidence" / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"1" * 16)
    (folder / "a.yaml").write_text(
        "topic:\n  key: a\n  title: A\nevents:\n"
        "  - key: a1\n    t0: 2026-10-14T08:00:00+02:00\n    phase: Exploit\n    actor: X\n    action: eins\n    status: open\n"
        "    evidence:\n      - file: evidence/shot.png\n        type: screenshot\n",
        encoding="utf-8",
    )
    db = tmp_path / "chronicle.db"
    import_folder(db, folder)

    (folder / "evidence" / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"2" * 16)  # same file name, new content
    (report,) = import_folder(db, folder)
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT version FROM events WHERE key = 'a1'").fetchone()[0] == 2
    conn.close()
    assert report.updated == ["a1"]
