"""Operations data (accounts, sessions, audit log, settings) lives in its own file, beside the index (issue #89)."""

import shutil
import sqlite3
from pathlib import Path

from chronicle import accounts, security
from chronicle.db import connect, ops_path
from chronicle.importer import import_folder
from chronicle.repository import list_events

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def tables_of(path):
    conn = sqlite3.connect(path)
    try:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        conn.close()


def test_accounts_and_audit_are_in_the_operations_file_not_in_the_index(tmp_path):
    db = tmp_path / "chronicle.db"
    conn = connect(db)
    accounts.create_user(conn, "anna", security.hash_password("correct horse battery"), "analyst")
    conn.close()

    assert {"users", "sessions", "audit_log"} <= tables_of(ops_path(db))
    assert not ({"users", "sessions", "audit_log", "settings", "login_failures"} & tables_of(db))


def test_the_operations_file_is_next_to_the_index():
    assert ops_path(Path("/data/chronicle.db")) == Path("/data/chronicle.ops.db")


def test_a_database_written_before_the_split_is_moved_once(tmp_path):
    db = tmp_path / "chronicle.db"
    legacy = sqlite3.connect(db)
    legacy.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE, "
                   "password_hash TEXT NOT NULL, role TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, "
                   "created_at TEXT NOT NULL DEFAULT '')")
    legacy.execute("INSERT INTO users (username, password_hash, role) VALUES ('altkonto', 'x', 'admin')")
    legacy.commit()
    legacy.close()

    conn = connect(db)
    assert accounts.get_user_by_name(conn, "altkonto") is not None
    conn.close()
    assert "users" not in tables_of(db)

    conn = connect(db)  # a second start changes nothing
    assert accounts.get_user_by_name(conn, "altkonto") is not None
    conn.close()


def test_a_lost_index_is_rebuilt_from_the_files_and_the_accounts_stay(tmp_path):
    folder = tmp_path / "data"
    shutil.copytree(DATA_DIR, folder)
    db = tmp_path / "chronicle.db"
    conn = connect(db)
    accounts.create_user(conn, "anna", security.hash_password("correct horse battery"), "analyst")
    conn.close()
    import_folder(db, folder)

    db.unlink()  # the index is gone, the operations file is not
    import_folder(db, folder)

    conn = connect(db)
    assert len(list_events(conn)) == 7
    assert accounts.get_user_by_name(conn, "anna") is not None
    conn.close()
