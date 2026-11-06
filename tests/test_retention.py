"""Retention of the ops tables that anonymous traffic can write (login_failures, audit_log).

Every failed login, unlock or setup guess inserts rows; before the retention fix nothing ever removed
them, so an unauthenticated visitor could grow the ops database without end (security audit run-1:
chronicle/accounts.py:login_failures:no-retention-purge and chronicle/accounts.py:audit_log:no-retention-purge).
"""

import pytest

from chronicle import accounts, security
from chronicle.db import connect

PASSWORD = "correct horse battery"


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / "ops-retention.db")
    accounts.create_user(connection, "anna", security.hash_password(PASSWORD), "analyst")
    yield connection
    connection.close()


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def insert_failure(conn, key, age_seconds=0):
    conn.execute("INSERT INTO login_failures (key, ts) VALUES (?, ?)", (key, accounts.now() - age_seconds))
    conn.commit()


class TestLoginFailuresRetention:
    def test_every_write_removes_expired_rows(self, conn):
        expired = accounts.now() - (accounts.FAILURE_WINDOW_SECONDS + 5)
        conn.executemany(
            "INSERT INTO login_failures (key, ts) VALUES (?, ?)",
            [("pair:anna|203.0.113.9", expired), ("ip:203.0.113.9", expired), ("setup", expired)],
        )
        conn.commit()

        accounts.record_login_failure(conn, "anna", "198.51.100.7")

        remaining = {row["key"] for row in conn.execute("SELECT key FROM login_failures")}
        assert remaining == {"pair:anna|198.51.100.7", "ip:198.51.100.7"}

    def test_ip_rows_of_old_attempts_do_not_survive_forever(self, conn):
        # Before the fix, no code path ever deleted an ip:<address> row.
        insert_failure(conn, "ip:203.0.113.9", age_seconds=accounts.FAILURE_WINDOW_SECONDS + 1)
        accounts.record_login_failure(conn, "anna", "198.51.100.7")
        assert count(conn, "login_failures") == 2
        assert conn.execute("SELECT COUNT(*) FROM login_failures WHERE key = 'ip:203.0.113.9'").fetchone()[0] == 0

    def test_setup_failures_share_the_retention(self, conn):
        insert_failure(conn, "setup", age_seconds=accounts.FAILURE_WINDOW_SECONDS + 1)
        accounts.record_setup_failure(conn, ip="203.0.113.9")
        rows = {row["key"] for row in conn.execute("SELECT key FROM login_failures")}
        assert rows == {"setup"}

    def test_throttle_still_counts_only_the_live_window(self, conn):
        insert_failure(conn, "ip:203.0.113.9", age_seconds=accounts.FAILURE_WINDOW_SECONDS + 1)
        for _ in range(accounts.MAX_FAILED_LOGINS_PER_IP):
            insert_failure(conn, "ip:203.0.113.9")
        assert accounts.login_blocked(conn, "anna", "203.0.113.9") is True

        # Once the window has passed and any later failure ran the retention delete,
        # the same address is free again and the stale rows are gone.
        for row in conn.execute("SELECT key FROM login_failures").fetchall():
            conn.execute("UPDATE login_failures SET ts = ? WHERE key = ?", (accounts.now() - accounts.FAILURE_WINDOW_SECONDS - 1, row["key"]))
        conn.commit()
        accounts.record_login_failure(conn, "anna", "198.51.100.7")
        assert accounts.login_blocked(conn, "anna", "203.0.113.9") is False

    def test_growth_is_bounded_by_the_window(self, conn, monkeypatch):
        # Rows only matter for FAILURE_WINDOW_SECONDS; with a moving clock nothing survives
        # the window once a later failure triggers the retention delete.
        clock = {"now": accounts.now()}
        monkeypatch.setattr(accounts, "now", lambda: clock["now"])
        for index in range(1000):
            clock["now"] += 1
            accounts.record_login_failure(conn, "anna", f"10.0.{index // 250}.{index % 250}")

        clock["now"] += accounts.FAILURE_WINDOW_SECONDS + 1
        accounts.record_login_failure(conn, "anna", "203.0.113.9")
        remaining = {row["key"] for row in conn.execute("SELECT key FROM login_failures")}
        assert remaining == {"pair:anna|203.0.113.9", "ip:203.0.113.9"}


class TestAuditRetention:
    def test_attacker_text_is_capped(self, conn):
        accounts.audit(conn, "login_failed", target="x" * 5000, detail="y" * 5000, ip="203.0.113.9")
        row = conn.execute("SELECT * FROM audit_log").fetchone()
        assert len(row["target"]) == accounts.MAX_AUDIT_TEXT
        assert len(row["detail"]) == accounts.MAX_AUDIT_TEXT

    def test_short_entries_stay_verbatim(self, conn):
        accounts.audit(conn, "login_failed", target="anna", ip="203.0.113.9")
        row = conn.execute("SELECT target FROM audit_log").fetchone()
        assert row["target"] == "anna"

    def test_table_keeps_a_row_bound(self, conn, monkeypatch):
        monkeypatch.setattr(accounts, "AUDIT_RETENTION_ROWS", 5)
        for index in range(10):
            accounts.audit(conn, "login_failed", target=str(index))
        # The opportunistic delete runs on every insert: only the newest rows survive.
        assert count(conn, "audit_log") <= accounts.AUDIT_RETENTION_ROWS

    def test_blocked_sources_cannot_grow_the_table_past_the_bound(self, conn, monkeypatch):
        monkeypatch.setattr(accounts, "AUDIT_RETENTION_ROWS", 20)
        for _ in range(1000):
            accounts.audit(conn, "login_blocked", target="anna", ip="203.0.113.9")
        assert count(conn, "audit_log") <= accounts.AUDIT_RETENTION_ROWS
