"""Manage accounts from the command line.

    python -m chronicle.admin_cli --db ./chronicle.db create --username anna --role analyst
    python -m chronicle.admin_cli --db ./chronicle.db list
    python -m chronicle.admin_cli --db ./chronicle.db reset-password --username anna

Passwords are read with a hidden prompt, or from stdin with --password-stdin.
"""

from __future__ import annotations

import argparse
import getpass
import sqlite3
import sys
from pathlib import Path

from . import accounts, security
from .db import connect


def read_password(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\n")
    first = getpass.getpass("Passwort: ")
    second = getpass.getpass("Passwort wiederholen: ")
    if first != second:
        raise ValueError("Die Passwörter stimmen nicht überein")
    return first


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chronicle.admin_cli", description="Manage Chronicle accounts.")
    parser.add_argument("--db", required=True, type=Path, help="SQLite file")
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create", help="create an account")
    create.add_argument("--username", required=True)
    create.add_argument("--role", choices=accounts.ROLES, default="viewer")
    create.add_argument("--password-stdin", action="store_true")

    commands.add_parser("list", help="list accounts")

    reset = commands.add_parser("reset-password", help="set a new password and end the user's sessions")
    reset.add_argument("--username", required=True)
    reset.add_argument("--password-stdin", action="store_true")

    args = parser.parse_args(argv)
    conn = connect(args.db)
    try:
        if args.command == "list":
            for user in accounts.list_users(conn):
                state = "aktiv" if user["active"] else "deaktiviert"
                print(f"{user['id']:>4}  {user['username']:<24} {user['role']:<8} {state}")
            return 0

        row = accounts.get_user_by_name(conn, args.username)
        if args.command == "create":
            password = read_password(args.password_stdin)
            user_id = accounts.create_user(conn, args.username, security.hash_password(password), args.role, must_change=True)
            accounts.audit(conn, "user_created", target=args.username, detail=f"role={args.role}", ip="cli")
            print(f"Benutzer angelegt: id={user_id}, rolle={args.role}")
            return 0

        if args.command == "reset-password":
            if row is None:
                print("Fehler: Benutzer nicht gefunden", file=sys.stderr)
                return 1
            password = read_password(args.password_stdin)
            accounts.update_user(conn, row["id"], password_hash=security.hash_password(password), must_change_password=1)
            accounts.delete_sessions_of_user(conn, row["id"])
            accounts.audit(conn, "password_reset", target=args.username, ip="cli")
            print("Passwort gesetzt, bestehende Sitzungen beendet")
            return 0
    except (ValueError, sqlite3.IntegrityError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
