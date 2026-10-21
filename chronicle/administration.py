"""Accounts and login locks, as admins change them. The API and the admin pages both use these functions,
so the rules (last admin, password policy, sessions, audit) exist only once."""

from __future__ import annotations

import sqlite3

from . import accounts, repository, security


def create_account(conn: sqlite3.Connection, actor_id: int, username: str, password: str, role: str, ip: str) -> dict:
    try:
        accounts.validate_username(username)
        password_hash = security.hash_password(password)
    except security.PasswordPolicyError as exc:
        raise repository.Invalid(str(exc)) from exc
    except ValueError as exc:
        raise repository.Invalid(str(exc)) from exc
    try:
        # The admin chose the password, so the user must replace it at the first login.
        user_id = accounts.create_user(conn, username, password_hash, role, must_change=True)
    except sqlite3.IntegrityError as exc:
        raise repository.Conflict("Benutzername ist bereits vergeben") from exc
    except ValueError as exc:
        raise repository.Invalid(str(exc)) from exc

    accounts.audit(conn, "user_created", user_id=actor_id, target=username, detail=f"role={role}", ip=ip)
    return accounts.get_user(conn, user_id)


def update_account(
    conn: sqlite3.Connection,
    actor_id: int,
    user_id: int,
    ip: str,
    *,
    role: str | None = None,
    active: bool | None = None,
    password: str | None = None,
) -> dict:
    target = accounts.get_user(conn, user_id)
    if target is None:
        raise repository.NotFound("Benutzer nicht gefunden")

    changes: dict = {}
    if role is not None:
        changes["role"] = role
    if active is not None:
        changes["active"] = int(active)
    if password is not None:
        try:
            changes["password_hash"] = security.hash_password(password)
        except security.PasswordPolicyError as exc:
            raise repository.Invalid(str(exc)) from exc
        changes["must_change_password"] = 1  # the user chooses their own password at the next login

    # Never leave the system without an active admin.
    was_active_admin = target["role"] == "admin" and target["active"]
    loses_admin = (role is not None and role != "admin") or active is False
    if was_active_admin and loses_admin and accounts.count_active_admins(conn) <= 1:
        raise repository.Conflict("Der letzte aktive Admin kann nicht herabgestuft oder deaktiviert werden")

    accounts.update_user(conn, user_id, **changes)

    # Changing role, deactivating or resetting the password ends the user's sessions.
    if changes:
        accounts.delete_sessions_of_user(conn, user_id)
    if "password_hash" in changes:
        accounts.audit(conn, "password_reset", user_id=actor_id, target=target["username"], ip=ip)
    other_changes = sorted(name for name in changes if name not in ("password_hash", "must_change_password"))
    if other_changes:
        accounts.audit(conn, "user_updated", user_id=actor_id, target=target["username"],
                       detail=",".join(other_changes), ip=ip)
    return accounts.get_user(conn, user_id)


def unlock_account(conn: sqlite3.Connection, actor_id: int, user_id: int, ip: str) -> None:
    """Lift the login lock of one account for all clients. Locks by client address expire on their own."""
    target = accounts.get_user(conn, user_id)
    if target is None:
        raise repository.NotFound("Benutzer nicht gefunden")
    accounts.clear_account_failures(conn, target["username"])
    accounts.audit(conn, "login_unlocked", user_id=actor_id, target=target["username"], ip=ip)
