"""Fixtures shared by the tests: the example data copied into a temporary folder, with a database next to it."""

import shutil
from pathlib import Path

import pytest

from chronicle import accounts, security
from chronicle.db import connect
from chronicle.importer import import_folder

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PASSWORD = "correct horse battery"
B_TOPIC = (
    "topic:\n  key: b\n  title: B\nevents:\n  - key: b1\n    t0: 2026-10-14T10:00:00+02:00\n"
    "    phase: Analysis\n    actor: X\n    action: Schritt B\n    status: open\n"
)


def example_folder(db_path) -> Path:
    """A copy of the example data next to the database, as the data folder of an app. Made once per database."""
    copy = Path(db_path).parent / "data"
    if not any(copy.glob("*.yaml")):
        shutil.copytree(DATA_DIR, copy, dirs_exist_ok=True)
    return copy


def import_example(db_path) -> list:
    """Builds the index of `db_path` from the example data. The copy next to it is the data folder (ADR 0011)."""
    return import_folder(db_path, example_folder(db_path))


def app_for(db_path, **options):
    """The app on `db_path` with the data folder next to it, created empty when there is none. The files are the
    source of truth (ADR 0011), so every app has a data folder."""
    from chronicle.api import create_app

    data_dir = options.pop("data_dir", None) or Path(db_path).parent / "data"
    data_dir.mkdir(exist_ok=True)
    return create_app(db_path, data_dir=data_dir, **options)


@pytest.fixture
def folder(tmp_path):
    """The example data as a folder; the database is tmp_path / "chronicle.db"."""
    copy = tmp_path / "data"
    shutil.copytree(DATA_DIR, copy)
    import_folder(tmp_path / "chronicle.db", copy)
    return copy


@pytest.fixture
def folder_with_b(tmp_path):
    """The example data plus a second topic, b.yaml, with one event."""
    copy = tmp_path / "data"
    shutil.copytree(DATA_DIR, copy)
    (copy / "b.yaml").write_text(B_TOPIC, encoding="utf-8")
    import_folder(tmp_path / "chronicle.db", copy)
    return copy


@pytest.fixture
def conn(folder):
    connection = connect(folder.parent / "chronicle.db")
    yield connection
    connection.close()


@pytest.fixture
def api_client(tmp_path, folder):
    """Returns a function that logs in as "anna" with the given role and gives back an API client."""
    from fastapi.testclient import TestClient

    from chronicle.api import create_app

    def make(role="analyst"):
        conn = connect(tmp_path / "chronicle.db")
        accounts.create_user(conn, "anna", security.hash_password(PASSWORD), role)
        conn.close()
        client = TestClient(create_app(tmp_path / "chronicle.db", data_dir=folder))
        response = client.post("/api/v1/auth/login", json={"username": "anna", "password": PASSWORD})
        assert response.status_code == 200, response.text
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        return client

    return make
