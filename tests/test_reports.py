import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import app_for, example_folder, import_example
from chronicle import accounts, repository, security
from chronicle.db import connect
from chronicle.evidence import check_file
from chronicle.importer import import_folder
from chronicle.models import Event

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PASSWORD = "correct horse battery"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


@pytest.fixture(autouse=True)
def insecure_cookies(monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "reports.db"
    conn = connect(path)
    accounts.create_user(conn, "admin", security.hash_password(PASSWORD), "admin")
    anna_id = accounts.create_user(conn, "anna", security.hash_password(PASSWORD), "analyst")
    accounts.create_user(conn, "viewer", security.hash_password(PASSWORD), "viewer")
    conn.close()
    import_example(path)

    # Changes made after the import, written to the folder like every change in the app.
    data = example_folder(path)
    conn = connect(path)
    repository.create_event(
        conn,
        Event.model_validate({
            "key": "nur-in-db", "t0": "2026-10-15T10:00:00+02:00", "phase": "Analysis", "actor": "Analyst",
            "action": "Nur in der Datenbank", "status": "open",
        }),
        ["inc-2026-042"],
        anna_id,
        "127.0.0.1",
        data,
    )
    repository.add_evidence(conn, "exploit", check_file("probe.png", PNG), "screenshot", "Probe", anna_id, "127.0.0.1", data)
    conn.close()
    return path


def api_login(db_path, username):
    client = TestClient(app_for(db_path))
    response = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client


def test_admin_downloads_a_self_contained_report(db_path):
    admin = api_login(db_path, "admin")
    response = admin.get("/api/v1/topics/inc-2026-042/report")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["content-disposition"] == 'attachment; filename="bericht-inc-2026-042.html"'
    assert response.headers["x-content-type-options"] == "nosniff"

    html = response.text
    assert "Berichts-Snapshot" in html
    assert "Nur in der Datenbank" in html  # the report comes from the database
    assert "<style>" in html  # stylesheet inlined
    assert "data:image/png;base64," in html  # evidence embedded
    assert "/api/v1/evidence" not in html  # nothing that needs the server
    assert "app.css" not in html
    # vis-timeline and vis-network are part of the file, so the diagrams open without a network
    assert "vis-timeline" in html and "vis-network" in html
    assert not re.search(r'<(script|link)[^>]+(src|href)="(https:)?//cdn\.jsdelivr\.net/npm/vis-', html)
    assert 'src="/static/vendor' not in html


def test_report_includes_events_referenced_from_other_topics(db_path):
    folder = example_folder(db_path)
    (folder / "projekt.yaml").write_text(
        "topic:\n  key: projekt-x\n  title: Projekt X\n  kind: project\n"
        "events:\n"
        "  - key: proj-1\n    t0: 2026-10-20T09:00:00+02:00\n    phase: Lessons Learned\n"
        "    actor: Projektleitung\n    action: Projektereignis\n    status: open\n"
        "refs:\n  - exploit\n",
        encoding="utf-8",
    )
    import_folder(db_path, folder)

    html = api_login(db_path, "admin").get("/api/v1/topics/projekt-x/report").text
    assert "Projektereignis" in html
    assert "Remote Code Execution erfolgreich" in html


def test_report_is_written_to_the_audit_log_without_content(db_path):
    api_login(db_path, "admin").get("/api/v1/topics/inc-2026-042/report")

    conn = connect(db_path)
    entries = [entry for entry in accounts.list_audit(conn) if entry["action"] == "report_created"]
    conn.close()
    assert len(entries) == 1
    assert entries[0]["target"] == "inc-2026-042"
    assert entries[0]["detail"] == "evidence=3"


@pytest.mark.parametrize("username", ["anna", "viewer"])
def test_only_admins_can_build_reports(db_path, username):
    client = api_login(db_path, username)
    assert client.get("/api/v1/topics/inc-2026-042/report").status_code == 403
    assert client.get("/reports").status_code == 403
    assert client.get("/reports/inc-2026-042").status_code == 403


def test_anonymous_request_gets_no_report(db_path):
    client = TestClient(app_for(db_path))
    assert client.get("/api/v1/topics/inc-2026-042/report").status_code == 401
    response = client.get("/reports/inc-2026-042", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_unknown_topic_is_not_found(db_path):
    admin = api_login(db_path, "admin")
    assert admin.get("/api/v1/topics/gibt-es-nicht/report").status_code == 404
    assert admin.get("/reports/gibt-es-nicht").status_code == 404


def test_admin_sees_the_report_page_with_download_links(db_path):
    admin = api_login(db_path, "admin")
    page = admin.get("/reports")
    assert page.status_code == 200
    assert 'href="/reports/inc-2026-042"' in page.text
    assert 'href="/reports"' in admin.get("/events").text  # navigation entry for admins

    anna = api_login(db_path, "anna")
    assert 'href="/reports"' not in anna.get("/events").text


def pdf_pages(body: bytes) -> list[str]:
    import io

    from pypdf import PdfReader

    return [page.extract_text() for page in PdfReader(io.BytesIO(body)).pages]


def test_technical_pdf_holds_everything(db_path):
    response = api_login(db_path, "admin").get("/api/v1/topics/inc-2026-042/report.pdf")
    assert response.status_code == 200 and response.content.startswith(b"%PDF")
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == 'attachment; filename="technisch-inc-2026-042.pdf"'
    text = "\n".join(pdf_pages(response.content))
    assert "Ausnutzung von CVE-2026-XXXX" in text and "Nur in der Datenbank" in text  # from the database
    assert "Ausgelöst durch: CVE-2026-XXXX veröffentlicht" in text  # causes by title, not by key
    assert "Herstellerhinweis" in text and "Remote Code Execution" in text  # the details
    assert "probe.png" in text  # evidence is listed, even when the image itself cannot be placed


def test_management_pdf_has_titles_and_figures_but_no_details(db_path):
    response = api_login(db_path, "admin").get("/api/v1/topics/inc-2026-042/report.pdf?art=management")
    assert response.headers["content-disposition"] == 'attachment; filename="management-inc-2026-042.pdf"'
    text = "\n".join(pdf_pages(response.content))
    assert "MANAGEMENT SUMMARY" in text and "Zeitraum:" in text and "Events" in text
    assert "CVE-2026-XXXX veröffentlicht" in text and "Nur in der Datenbank" in text
    assert "Herstellerhinweis" not in text and "Ausgelöst durch" not in text and "probe.png" not in text


def test_pdf_places_real_images_in_the_technical_report_only(db_path):
    import io

    from PIL import Image

    image = io.BytesIO()
    Image.new("RGB", (40, 20), "red").save(image, "PNG")
    conn = connect(db_path)
    anna = conn.execute("SELECT id FROM users WHERE username = 'anna'").fetchone()["id"]
    repository.add_evidence(conn, "analysis", check_file("echt.png", image.getvalue()), "screenshot", "Echtes Bild", anna, "127.0.0.1", example_folder(db_path))
    conn.close()
    admin = api_login(db_path, "admin")
    response = admin.get("/api/v1/topics/inc-2026-042/report.pdf")
    assert response.status_code == 200 and b"/Subtype /Image" in response.content
    assert b"/Subtype /Image" not in admin.get("/api/v1/topics/inc-2026-042/report.pdf?art=management").content


def test_pdf_of_an_empty_topic_and_unknown_requests(db_path):
    admin = api_login(db_path, "admin")
    assert admin.post("/api/v1/topics", json={"key": "leer", "title": "Leeres Topic", "kind": "project"}).status_code == 201
    for art in ("technisch", "management"):
        text = "\n".join(pdf_pages(admin.get(f"/api/v1/topics/leer/report.pdf?art={art}").content))
        assert "Leeres Topic" in text and "Noch keine Events" in text
    assert admin.get("/api/v1/topics/gibt-es-nicht/report.pdf").status_code == 404
    assert admin.get("/api/v1/topics/leer/report.pdf?art=geheim").status_code == 404


def test_pdf_is_audited_with_its_kind_and_only_admins_get_it(db_path):
    api_login(db_path, "admin").get("/api/v1/topics/inc-2026-042/report.pdf?art=management")
    conn = connect(db_path)
    (entry,) = [e for e in accounts.list_audit(conn) if e["action"] == "report_created"]
    conn.close()
    assert entry["detail"].startswith("pdf=management")
    for username in ("anna", "viewer"):
        client = api_login(db_path, username)
        assert client.get("/api/v1/topics/inc-2026-042/report.pdf").status_code == 403
        assert client.get("/reports/inc-2026-042/pdf").status_code == 403
    assert TestClient(app_for(db_path)).get("/api/v1/topics/inc-2026-042/report.pdf").status_code == 401


def test_reports_page_offers_html_and_both_pdfs(db_path):
    admin = api_login(db_path, "admin")
    html = admin.get("/reports").text
    assert 'href="/reports/inc-2026-042/pdf?art=technisch"' in html and 'href="/reports/inc-2026-042/pdf?art=management"' in html
    page = admin.get("/reports/inc-2026-042/pdf?art=management")
    assert page.status_code == 200 and page.headers["content-type"] == "application/pdf"
