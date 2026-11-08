"""Regressions for the final hardening sweep of audit run-1, REPORT.md §5 (non-finding notes):

- update_account validates the role of the HTML form like the API's Literal
- the reading-lock account name "lesezugriff" is reserved
- the last-admin check and the update share one transaction; UPDATABLE_FIELDS is enforced on every path
- git output in SyncError is one bounded, control-free line
- event techniques are escaped in the PDF renderer (defense in depth behind the model gate)
- report downloads map Conflict/Invalid/EvidenceRejected to 409/422 pages instead of a 500, and carry a CSP
- the dashboard frame escapes ExportError text
- a search query without any word matches nothing, not the whole list
"""

import subprocess
from datetime import datetime, UTC
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from chronicle import accounts, administration, repository, security, sync
from chronicle.db import connect
from chronicle.evidence import EvidenceRejected
from chronicle.pdf_report import _event_block, _techniques
from chronicle.report import report_headers
from chronicle.repository import EventFilter
from chronicle.yamlscan import MAX_MESSAGE_CHARS
from tests.conftest import app_for, import_example

PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def insecure_cookies(monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "sweep.db"
    conn = connect(path)
    accounts.create_user(conn, "admin", security.hash_password(PASSWORD), "admin")
    accounts.create_user(conn, "anna", security.hash_password(PASSWORD), "analyst")
    conn.close()
    import_example(path)
    return path


def login(db_path, username="admin"):
    client = TestClient(app_for(db_path))
    response = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client


# --- roles and reserved names ----------------------------------------------------------------------------------------

def test_update_account_validates_the_role_of_the_form(db_path):
    conn = connect(db_path)
    anna = accounts.get_user_by_name(conn, "anna")
    with pytest.raises(repository.Invalid, match="Rolle"):
        administration.update_account(conn, 1, anna["id"], "127.0.0.1", role="root")
    assert accounts.get_user(conn, anna["id"])["role"] == "analyst"  # nothing was written
    updated = administration.update_account(conn, 1, anna["id"], "127.0.0.1", role="viewer")
    assert updated["role"] == "viewer"
    conn.close()


def test_the_reading_lock_name_is_reserved_everywhere(db_path):
    conn = connect(db_path)
    for name in ("lesezugriff", "LeseZugriff", "LESEZUGRIFF"):
        with pytest.raises(repository.Invalid, match="reserviert"):
            administration.create_account(conn, 1, name, PASSWORD, "admin", "127.0.0.1")
        with pytest.raises(ValueError, match="reserviert"):
            accounts.create_user(conn, name, "x", "viewer")
    # the app itself can still create the lock account
    assert accounts.get_user_by_name(conn, accounts.GUEST_USERNAME) is None
    guest_id = accounts.ensure_guest(conn)
    assert accounts.get_user(conn, guest_id)["username"] == "lesezugriff"
    conn.close()


# --- last admin in one transaction -----------------------------------------------------------------------------------

def test_the_last_admin_cannot_be_demoted_and_nothing_is_written(db_path):
    conn = connect(db_path)
    admin = accounts.get_user_by_name(conn, "admin")
    with pytest.raises(repository.Conflict, match="letzte aktive Admin"):
        administration.update_account(conn, admin["id"], admin["id"], "127.0.0.1", role="analyst")
    with pytest.raises(repository.Conflict, match="letzte aktive Admin"):
        administration.update_account(conn, admin["id"], admin["id"], "127.0.0.1", active=False)
    fresh = accounts.get_user(conn, admin["id"])
    assert fresh["role"] == "admin" and fresh["active"] == 1
    conn.close()


def test_with_two_admins_one_demotion_is_allowed(db_path):
    conn = connect(db_path)
    admin = accounts.get_user_by_name(conn, "admin")
    accounts.create_user(conn, "zweiter", security.hash_password(PASSWORD), "admin")
    administration.update_account(conn, admin["id"], admin["id"], "127.0.0.1", role="analyst")
    assert accounts.get_user(conn, admin["id"])["role"] == "analyst"
    assert accounts.count_active_admins(conn) == 1
    conn.close()


def test_updatable_fields_are_enforced_on_every_path(db_path):
    conn = connect(db_path)
    anna = accounts.get_user_by_name(conn, "anna")
    with pytest.raises(ValueError, match="Nicht änderbare Felder"):
        accounts.update_user(conn, anna["id"], role="viewer", evil="x")
    with pytest.raises(ValueError, match="Nicht änderbare Felder"):
        accounts.apply_user_changes(conn, anna["id"], {"role = 'admin' --": "x"})
    assert accounts.get_user(conn, anna["id"])["role"] == "analyst"
    conn.close()


# --- bounded git output in SyncError ---------------------------------------------------------------------------------

def test_git_message_is_one_bounded_line_without_control_characters():
    hostile = "fatal: \x1b[31mrot\x1b[0m\n" + "x" * 500 + "\r\nsecret path /etc/passwd"
    result = subprocess.CompletedProcess(args=["git"], returncode=128, stdout="", stderr=hostile)
    message = sync._git_message(result)
    assert "\n" not in message and "\r" not in message and "\x1b" not in message
    assert len(message) <= MAX_MESSAGE_CHARS  # the tail (with /etc/passwd) is cut off — that is the bound
    assert message.startswith("fatal:") and "xxxx" in message


def test_git_message_falls_back_to_the_exit_code():
    result = subprocess.CompletedProcess(args=["git"], returncode=42, stdout="", stderr="")
    assert sync._git_message(result) == "Exit-Code 42"


# --- PDF escapes techniques (defense in depth behind the model gate) --------------------------------------------------

def hostile_event():
    from chronicle.models import Phase, Status

    return SimpleNamespace(
        key="e1", t0=datetime(2026, 10, 14, 8, tzinfo=UTC), t1=None, detected_at=None, phase=Phase.ANALYSIS,
        status=Status.OPEN, actor="SIEM", action="Schritt", system=None,
        techniques=["<script>alert(1)</script>"], tags=[], detection=None, entities=[], comments=[], triggered_by=[],
    )


def test_pdf_technique_tables_and_lines_escape_markup():
    table_paragraphs = [cell for row in _techniques([hostile_event()])[-1]._cellvalues for cell in row]  # type: ignore[attr-defined]
    joined = " ".join(getattr(cell, "text", "") for cell in table_paragraphs)
    assert "<script>" not in joined and "&lt;script&gt;" in joined

    block_texts = [getattr(flowable, "text", "") for flowable in _event_block(hostile_event(), [], {})]
    joined = " ".join(block_texts)
    assert "<script>" not in joined and "&lt;script&gt;" in joined


# --- report downloads: status mapping and CSP -------------------------------------------------------------------------

def test_report_download_maps_refusals_to_status_pages(monkeypatch, db_path):
    import chronicle.page_routes.reports as reports_pages

    def refuse(exc):
        def _raise(*args, **kwargs):
            raise exc
        return _raise

    client = login(db_path)
    monkeypatch.setattr(reports_pages, "make_report", refuse(repository.Conflict("geändert")))
    response = client.get("/reports/inc-2026-042")
    assert response.status_code == 409 and "geändert" in response.text

    monkeypatch.setattr(reports_pages, "make_report", refuse(EvidenceRejected("passt nicht")))
    response = client.get("/reports/inc-2026-042")
    assert response.status_code == 422 and "passt nicht" in response.text

    monkeypatch.setattr(reports_pages, "make_pdf_report", refuse(repository.Conflict("geändert")))
    response = client.get("/reports/inc-2026-042/pdf")
    assert response.status_code == 409 and "geändert" in response.text

    monkeypatch.setattr(reports_pages, "make_pdf_report", refuse(repository.NotFound("unbekannt")))
    response = client.get("/reports/inc-2026-042/pdf")
    assert response.status_code == 404


def test_report_headers_carry_a_restrictive_csp():
    headers = report_headers("bericht-x.html")
    csp = headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp and "img-src data:" in csp and headers["X-Content-Type-Options"] == "nosniff"


def test_dashboard_frame_escapes_export_error_text(monkeypatch, db_path):
    import chronicle.page_routes.topics as topics_pages
    from chronicle.exporter import ExportError

    def _raise(*args, **kwargs):
        raise ExportError("<b>fett</b>")
    monkeypatch.setattr(topics_pages, "make_dashboard", _raise)
    response = login(db_path).get("/topics/inc-2026-042/dashboard")
    assert response.status_code == 409
    assert "<b>fett</b>" not in response.text and "&lt;b&gt;fett&lt;/b&gt;" in response.text


# --- search: a query without words matches nothing --------------------------------------------------------------------

def test_a_query_without_any_word_returns_no_events(db_path):
    conn = connect(db_path)
    total = repository.count_events(conn, EventFilter())
    assert total > 0
    for query in ("!!!", " ... ", "---"):
        assert repository.find_events(conn, EventFilter(query=query)) == []
        assert repository.count_events(conn, EventFilter(query=query)) == 0
    # unchanged behavior: no query or a word still works
    assert repository.count_events(conn, EventFilter(query="")) == total
    assert repository.count_events(conn, EventFilter()) == total
    conn.close()
