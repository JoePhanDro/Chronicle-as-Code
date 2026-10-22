"""Users, sessions, login throttling and audit log. All queries are parameterized."""

from __future__ import annotations

import re
import sqlite3
import time

from .security import hash_token, new_token

ROLES = ("admin", "analyst", "viewer")
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{3,64}$")
SESSION_TTL_SECONDS = 8 * 60 * 60
# Failures are counted per (account, client IP) and per client IP, never per account alone.
# Otherwise anyone who knows a username could lock that person out (issue #27).
MAX_FAILED_LOGINS = 5
MAX_FAILED_LOGINS_PER_IP = 20
FAILURE_WINDOW_SECONDS = 15 * 60
# The reading lock (issue #13): without an account, a visitor reads the data after entering the lock password.
# That session belongs to this account. Its password hash matches no password, so it cannot log in directly.
GUEST_USERNAME = "lesezugriff"
UNUSABLE_HASH = "!"
LOCK_SETTING = "reading_lock_hash"
# Failed unlocks are counted like failed logins. The parentheses keep the key apart from any account name.
LOCK_FAILURE_KEY = "(sperre)"


def now() -> int:
    return int(time.time())


def validate_username(username: str) -> None:
    if not USERNAME_PATTERN.match(username):
        raise ValueError("Benutzername: 3 bis 64 Zeichen, nur Buchstaben, Ziffern, Punkt, Unterstrich und Bindestrich")


def validate_role(role: str) -> None:
    if role not in ROLES:
        raise ValueError(f"Rolle muss eine von {', '.join(ROLES)} sein")


# Users

def create_user(conn: sqlite3.Connection, username: str, password_hash: str, role: str, must_change: bool = False) -> int:
    """`must_change` marks a password that was set by someone else; the user must replace it at the next login."""
    validate_username(username)
    validate_role(role)
    with conn:
        cursor = conn.execute(
            "INSERT INTO users (username, password_hash, role, must_change_password) VALUES (?, ?, ?, ?)",
            (username, password_hash, role, int(must_change)),
        )
    return cursor.lastrowid


def password_hash_of(conn: sqlite3.Connection, user_id: int) -> str | None:
    row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
    return row["password_hash"] if row else None


def set_password(conn: sqlite3.Connection, user_id: int, password_hash: str, must_change: bool) -> None:
    with conn:
        conn.execute(
            "UPDATE users SET password_hash = ?, must_change_password = ? WHERE id = ?",
            (password_hash, int(must_change), user_id),
        )


USER_COLUMNS = "id, username, role, active, must_change_password, created_at"


def get_user(conn: sqlite3.Connection, user_id: int) -> dict | None:
    row = conn.execute(f"SELECT {USER_COLUMNS} FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def get_user_by_name(conn: sqlite3.Connection, username: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, username, role, active, must_change_password, password_hash FROM users WHERE username = ?", (username,)
    ).fetchone()


def list_users(conn: sqlite3.Connection) -> list[dict]:
    return [dict(row) for row in conn.execute(f"SELECT {USER_COLUMNS} FROM users ORDER BY id").fetchall()]


def count_users(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def create_first_admin(conn: sqlite3.Connection, username: str, password_hash: str) -> int | None:
    """Create the first account, as admin. Returns None if an account exists; the check and the insert share one transaction."""
    validate_username(username)
    conn.execute("BEGIN IMMEDIATE")
    try:
        if count_users(conn) > 0:
            conn.execute("ROLLBACK")
            return None
        cursor = conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'admin')",
            (username, password_hash),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return cursor.lastrowid


def count_active_admins(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = 1").fetchone()[0]


UPDATABLE_FIELDS = {"role", "active", "password_hash", "must_change_password"}


def update_user(conn: sqlite3.Connection, user_id: int, **fields) -> None:
    if not set(fields) <= UPDATABLE_FIELDS:
        raise ValueError(f"Nicht änderbare Felder: {set(fields) - UPDATABLE_FIELDS}")
    if not fields:
        return
    assignments = ", ".join(f"{name} = ?" for name in fields)
    with conn:
        conn.execute(f"UPDATE users SET {assignments} WHERE id = ?", (*fields.values(), user_id))


# Sessions: the cookie carries a random token, the database only stores its hash.

def create_session(conn: sqlite3.Connection, user_id: int) -> tuple[str, str]:
    token = new_token()
    csrf_token = new_token()
    with conn:
        conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now(),))
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, csrf_token, expires_at) VALUES (?, ?, ?, ?)",
            (hash_token(token), user_id, csrf_token, now() + SESSION_TTL_SECONDS),
        )
    return token, csrf_token


def find_session(conn: sqlite3.Connection, token: str) -> dict | None:
    row = conn.execute(
        """
        SELECT users.id, users.username, users.role, users.must_change_password, sessions.csrf_token
        FROM sessions JOIN users ON users.id = sessions.user_id
        WHERE sessions.token_hash = ? AND sessions.expires_at > ? AND users.active = 1
        """,
        (hash_token(token), now()),
    ).fetchone()
    if row is None:
        return None
    session = dict(row)
    session["guest"] = session["username"] == GUEST_USERNAME
    session["reading_lock"] = reading_lock_enabled(conn)
    return session


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    with conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def delete_setting(conn: sqlite3.Connection, key: str) -> None:
    with conn:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))


def reading_lock_enabled(conn: sqlite3.Connection) -> bool:
    return get_setting(conn, LOCK_SETTING) is not None


def ensure_guest(conn: sqlite3.Connection) -> int:
    """The account behind unlocked reading sessions. Created on first use, switched on and off with the lock."""
    row = get_user_by_name(conn, GUEST_USERNAME)
    if row is None:
        return create_user(conn, GUEST_USERNAME, UNUSABLE_HASH, "viewer")
    return row["id"]


def delete_session(conn: sqlite3.Connection, token: str) -> None:
    with conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (hash_token(token),))


def delete_sessions_of_user(conn: sqlite3.Connection, user_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def delete_other_sessions(conn: sqlite3.Connection, user_id: int, keep_token: str) -> None:
    """End every session of the user except the one with `keep_token`."""
    with conn:
        conn.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?", (user_id, hash_token(keep_token)))


# Login throttling. A lock affects only the source that caused it:
# - pair:<account>|<ip> locks one account for one client, after MAX_FAILED_LOGINS failures
# - ip:<ip> locks one client across all accounts, after MAX_FAILED_LOGINS_PER_IP failures
# Nothing locks an account for everybody, so a third party cannot lock the owner out.

def _pair_key(username: str, ip: str) -> str:
    return f"pair:{username.lower()}|{ip}"


def _ip_key(ip: str) -> str:
    return f"ip:{ip}"


def _count_since(conn: sqlite3.Connection, key: str, since: int) -> int:
    return conn.execute("SELECT COUNT(*) FROM login_failures WHERE key = ? AND ts > ?", (key, since)).fetchone()[0]


def login_blocked(conn: sqlite3.Connection, username: str, ip: str) -> bool:
    since = now() - FAILURE_WINDOW_SECONDS
    if _count_since(conn, _pair_key(username, ip), since) >= MAX_FAILED_LOGINS:
        return True
    return _count_since(conn, _ip_key(ip), since) >= MAX_FAILED_LOGINS_PER_IP


def record_login_failure(conn: sqlite3.Connection, username: str, ip: str) -> None:
    with conn:
        conn.executemany(
            "INSERT INTO login_failures (key, ts) VALUES (?, ?)",
            [(_pair_key(username, ip), now()), (_ip_key(ip), now())],
        )


def clear_account_failures(conn: sqlite3.Connection, username: str) -> None:
    """Lift the lock of one account for every client. Used by admins."""
    prefix = f"pair:{username.lower()}|"
    with conn:
        conn.execute("DELETE FROM login_failures WHERE substr(key, 1, ?) = ?", (len(prefix), prefix))


def clear_login_failures(conn: sqlite3.Connection, username: str, ip: str) -> None:
    # Only the pair of this account and this client; the IP counter stays.
    with conn:
        conn.execute("DELETE FROM login_failures WHERE key = ?", (_pair_key(username, ip),))


# Audit log: records who did what, never passwords or tokens.

def audit(
    conn: sqlite3.Connection,
    action: str,
    user_id: int | None = None,
    target: str | None = None,
    detail: str | None = None,
    ip: str | None = None,
) -> None:
    with conn:
        conn.execute(
            "INSERT INTO audit_log (ts, user_id, action, target, detail, ip) VALUES (?, ?, ?, ?, ?, ?)",
            (now(), user_id, action, target, detail, ip),
        )


def list_audit(
    conn: sqlite3.Connection,
    limit: int = 200,
    action: str | None = None,
    actor: str | None = None,
    since: int | None = None,
) -> list[dict]:
    """Newest first. Filters are optional; `since` is a Unix timestamp in seconds."""
    conditions: list[str] = []
    params: list = []
    if action:
        conditions.append("audit_log.action = ?")
        params.append(action)
    if actor:
        conditions.append("users.username = ? COLLATE NOCASE")
        params.append(actor)
    if since is not None:
        conditions.append("audit_log.ts >= ?")
        params.append(since)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = conn.execute(
        f"""
        SELECT audit_log.pk, audit_log.ts, audit_log.action, audit_log.target,
               audit_log.detail, audit_log.ip, users.username AS actor
        FROM audit_log LEFT JOIN users ON users.id = audit_log.user_id
        {where}
        ORDER BY audit_log.pk DESC LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    return [dict(row) for row in rows]
