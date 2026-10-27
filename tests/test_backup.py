"""The backup copies the index and the operations file, owner-only, and refuses to overwrite (issue #89)."""

import sqlite3
import stat
from pathlib import Path

import pytest

from chronicle.backup import BackupError, backup_database, main
from chronicle.db import ops_path


def test_the_backup_holds_the_index_and_the_operations_file(conn, folder, tmp_path):
    target = tmp_path / "copy" / "chronicle.db"
    written = backup_database(folder.parent / "chronicle.db", target)

    assert written == target
    assert ops_path(target).is_file()
    copy = sqlite3.connect(target)
    assert copy.execute("SELECT COUNT(*) FROM events").fetchone()[0] > 0
    copy.close()
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_a_missing_database_is_refused(tmp_path):
    with pytest.raises(BackupError, match="nicht gefunden"):
        backup_database(tmp_path / "gibt-es-nicht.db", tmp_path / "copy.db")


def test_an_existing_target_is_never_overwritten(conn, folder, tmp_path):
    target = tmp_path / "copy.db"
    target.write_bytes(b"alt")
    with pytest.raises(BackupError, match="existiert bereits"):
        backup_database(folder.parent / "chronicle.db", target)
    assert target.read_bytes() == b"alt"


def test_a_file_that_is_no_database_leaves_no_copy_behind(tmp_path):
    source = tmp_path / "kaputt.db"
    source.write_bytes(b"das ist keine Datenbank" * 100)
    target = tmp_path / "copy.db"
    with pytest.raises(BackupError, match="Backup fehlgeschlagen"):
        backup_database(source, target)
    assert not target.exists()


def test_the_command_writes_a_timestamped_file_into_a_folder(conn, folder, tmp_path, capsys):
    out = tmp_path / "backups"
    out.mkdir()
    assert main(["--db", str(folder.parent / "chronicle.db"), "--out", str(out)]) == 0
    written = capsys.readouterr().out.split(": ", 1)[1].strip()
    assert written.startswith(str(out / "chronicle-")) and Path(written).is_file()
    assert ops_path(Path(written)).is_file()


def test_the_command_reports_an_error_with_exit_code_1(tmp_path, capsys):
    assert main(["--db", str(tmp_path / "fehlt.db"), "--out", str(tmp_path / "copy.db")]) == 1
    assert "Fehler" in capsys.readouterr().err
