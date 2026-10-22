"""The reading lock (issue #13). With a lock password set, visitors without an account can read after entering it.

Until then the server sends no data. Unlocking creates a session for the guest account, with the viewer role, so
every page and API check that already exists applies. Editing still needs an account. Setting, changing and
clearing the lock is for admins, see admin_pages.py.
"""

from __future__ import annotations

import sqlite3

from . import accounts, repository, security


class Refused(Exception):
    """The lock was not unlocked. The HTTP status and message go to the visitor."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def set_lock(conn: sqlite3.Connection, password: str, actor_id: int, ip: str) -> None:
    """Sets or changes the lock password. Sessions unlocked with the old password end."""
    try:
        password_hash = security.hash_password(password)
    except security.PasswordPolicyError as exc:
        raise repository.Invalid(str(exc)) from exc
    accounts.set_setting(conn, accounts.LOCK_SETTING, password_hash)
    guest = accounts.ensure_guest(conn)
    accounts.update_user(conn, guest, active=1)
    accounts.delete_sessions_of_user(conn, guest)
    accounts.audit(conn, "reading_lock_set", user_id=actor_id, ip=ip)


def clear_lock(conn: sqlite3.Connection, actor_id: int, ip: str) -> None:
    """Removes the lock. Reading then needs an account again, and the guest account is switched off."""
    accounts.delete_setting(conn, accounts.LOCK_SETTING)
    guest = accounts.get_user_by_name(conn, accounts.GUEST_USERNAME)
    if guest is not None:
        accounts.update_user(conn, guest["id"], active=0)
        accounts.delete_sessions_of_user(conn, guest["id"])
    accounts.audit(conn, "reading_lock_cleared", user_id=actor_id, ip=ip)


def unlock(conn: sqlite3.Connection, password: str, ip: str) -> str:
    """Checks the lock password and returns the token of a new reading session."""
    stored = accounts.get_setting(conn, accounts.LOCK_SETTING)
    if stored is None:
        raise Refused(404, "Die Ansicht ist nicht gesperrt")
    if accounts.login_blocked(conn, accounts.LOCK_FAILURE_KEY, ip):
        accounts.audit(conn, "unlock_blocked", ip=ip)
        raise Refused(429, "Zu viele Fehlversuche. Bitte später erneut versuchen.")

    if not security.verify_password(stored, password):
        accounts.record_login_failure(conn, accounts.LOCK_FAILURE_KEY, ip)
        accounts.audit(conn, "unlock_failed", ip=ip)
        raise Refused(401, "Sperr-Passwort falsch")

    accounts.clear_login_failures(conn, accounts.LOCK_FAILURE_KEY, ip)
    guest = accounts.ensure_guest(conn)
    token, _ = accounts.create_session(conn, guest)
    accounts.audit(conn, "unlock", user_id=guest, ip=ip)
    return token
