"""python -m chronicle.importer: reports what it did, and a failed check is an error message, not a traceback."""

import subprocess
import sys

from chronicle.importer import main
from tests.conftest import DATA_DIR


def test_the_command_reports_the_imported_topics(tmp_path, capsys):
    assert main(["--db", str(tmp_path / "chronicle.db"), "--data", str(DATA_DIR)]) == 0
    out = capsys.readouterr().out
    assert "Topic importiert" in out and "angelegt: 7" in out


def test_a_failed_check_names_each_problem(tmp_path, capsys):
    folder = tmp_path / "data"
    folder.mkdir()
    (folder / "a.yaml").write_text("topic:\n  key: a\n", encoding="utf-8")  # title is missing
    db = tmp_path / "chronicle.db"

    assert main(["--db", str(db), "--data", str(folder)]) == 1
    assert "Fehler" in capsys.readouterr().err
    assert not db.exists()


def test_a_missing_folder_is_an_error_message(tmp_path, capsys):
    assert main(["--db", str(tmp_path / "chronicle.db"), "--data", str(tmp_path / "fehlt")]) == 1
    assert "Datenordner existiert nicht" in capsys.readouterr().err


def test_python_dash_m_chronicle_starts_the_command_line():
    result = subprocess.run([sys.executable, "-m", "chronicle", "--help"], capture_output=True, text=True)
    assert result.returncode == 0 and "usage" in result.stdout.lower()
