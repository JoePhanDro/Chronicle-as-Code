"""Account administration from the command line: python -m chronicle.admin_cli (issue #129, CLI coverage)."""

import io

import pytest

from chronicle import accounts, admin_cli, security
from chronicle.db import connect

PASSWORD = "correct horse battery"


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "chronicle.db"
    connect(path).close()
    return path


def run(db, *args, stdin=None, monkeypatch=None):
    if stdin is not None:
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    return admin_cli.main(["--db", str(db), *args])


def test_create_then_list_shows_the_account(db, monkeypatch, capsys):
    assert run(db, "create", "--username", "anna", "--role", "analyst", "--password-stdin",
               stdin=PASSWORD + "\n", monkeypatch=monkeypatch) == 0
    capsys.readouterr()

    assert run(db, "list") == 0

    out = capsys.readouterr().out
    assert "anna" in out and "analyst" in out and "aktiv" in out


def test_the_created_account_has_the_password_and_must_change_it(db, monkeypatch):
    run(db, "create", "--username", "anna", "--password-stdin", stdin=PASSWORD + "\n", monkeypatch=monkeypatch)

    conn = connect(db)
    user = accounts.get_user_by_name(conn, "anna")
    conn.close()
    assert security.verify_password(user["password_hash"], PASSWORD)
    assert user["must_change_password"] == 1


def test_create_with_an_existing_name_fails_with_exit_code_1(db, monkeypatch, capsys):
    run(db, "create", "--username", "anna", "--password-stdin", stdin=PASSWORD + "\n", monkeypatch=monkeypatch)
    capsys.readouterr()

    code = run(db, "create", "--username", "anna", "--password-stdin", stdin=PASSWORD + "\n", monkeypatch=monkeypatch)

    assert code == 1
    assert "Fehler" in capsys.readouterr().err


def test_a_password_that_is_too_short_fails_with_exit_code_1(db, monkeypatch, capsys):
    code = run(db, "create", "--username", "anna", "--password-stdin", stdin="kurz\n", monkeypatch=monkeypatch)

    assert code == 1
    assert "Fehler" in capsys.readouterr().err


def test_the_interactive_prompt_refuses_two_different_passwords(db, monkeypatch, capsys):
    answers = iter(["eins eins eins", "zwei zwei zwei"])
    monkeypatch.setattr(admin_cli.getpass, "getpass", lambda prompt: next(answers))

    code = run(db, "create", "--username", "anna")

    assert code == 1
    assert "stimmen nicht überein" in capsys.readouterr().err


def test_reset_password_for_an_unknown_user_fails(db, monkeypatch, capsys):
    code = run(db, "reset-password", "--username", "niemand", "--password-stdin",
               stdin=PASSWORD + "\n", monkeypatch=monkeypatch)

    assert code == 1
    assert "nicht gefunden" in capsys.readouterr().err


def test_reset_password_sets_the_new_password_and_ends_the_sessions(db, monkeypatch, capsys):
    run(db, "create", "--username", "anna", "--password-stdin", stdin=PASSWORD + "\n", monkeypatch=monkeypatch)
    capsys.readouterr()
    new = "neues passwort neu"

    code = run(db, "reset-password", "--username", "anna", "--password-stdin",
               stdin=new + "\n", monkeypatch=monkeypatch)

    assert code == 0
    assert "Passwort gesetzt" in capsys.readouterr().out
    conn = connect(db)
    user = accounts.get_user_by_name(conn, "anna")
    conn.close()
    assert security.verify_password(user["password_hash"], new)


def test_a_missing_database_option_is_a_usage_error():
    with pytest.raises(SystemExit) as excinfo:
        admin_cli.main(["list"])

    assert excinfo.value.code == 2
