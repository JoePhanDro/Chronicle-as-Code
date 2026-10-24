"""Starting the API server: python -m chronicle.serve passes its options to uvicorn (issue #129, CLI coverage)."""

import subprocess
import sys
from pathlib import Path

import pytest

from chronicle import serve

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def started(monkeypatch):
    """Replaces uvicorn.run and records how it was called. The environment is restored after the test."""
    calls = []
    monkeypatch.setattr(serve.uvicorn, "run", lambda app, **options: calls.append((app, options)))
    for name in ("CHRONICLE_DB", "CHRONICLE_DATA"):
        monkeypatch.setenv(name, "")
    return calls


def test_the_server_gets_the_database_and_the_default_address(tmp_path, started):
    code = serve.main(["--db", str(tmp_path / "x.db")])

    assert code == 0
    app, options = started[0]
    assert app == "chronicle.api:app"
    assert options["host"] == "127.0.0.1" and options["port"] == 8000
    assert serve.os.environ["CHRONICLE_DB"] == str(tmp_path / "x.db")


def test_a_data_folder_is_passed_on_as_the_source_of_truth(tmp_path, started):
    serve.main(["--db", str(tmp_path / "x.db"), "--data", str(tmp_path / "data"),
                "--host", "0.0.0.0", "--port", "9000"])

    _, options = started[0]
    assert options["host"] == "0.0.0.0" and options["port"] == 9000
    assert serve.os.environ["CHRONICLE_DATA"] == str(tmp_path / "data")


def test_the_database_option_is_required(started):
    with pytest.raises(SystemExit) as excinfo:
        serve.main([])

    assert excinfo.value.code == 2
    assert started == []


def test_python_m_chronicle_runs_the_command_line():
    result = subprocess.run([sys.executable, "-m", "chronicle", "schema"], cwd=ROOT,
                            capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()
