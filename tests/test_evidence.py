import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import app_for, import_example
from chronicle import accounts, security
from chronicle.db import connect
from chronicle.evidence import MAX_EVIDENCE_BYTES, EvidenceRejected, check_file, delivery_headers

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PASSWORD = "correct horse battery"

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 32
GIF = b"GIF89a" + b"0" * 32
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"0" * 32
PDF = b"%PDF-1.7\n" + b"0" * 32
TEXT = "Log-Zeile mit Umlaut äöü\n".encode("utf-8")
SVG = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"


@pytest.fixture(autouse=True)
def insecure_cookies(monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "evidence.db"
    conn = connect(path)
    accounts.create_user(conn, "admin", security.hash_password(PASSWORD), "admin")
    accounts.create_user(conn, "anna", security.hash_password(PASSWORD), "analyst")
    accounts.create_user(conn, "viewer", security.hash_password(PASSWORD), "viewer")
    conn.close()
    import_example(path)
    return path


def api_login(db_path, username):
    client = TestClient(app_for(db_path))
    response = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client


def evidence_id(db_path, event_key):
    conn = connect(db_path)
    try:
        row = conn.execute(
            """
            SELECT evidence.pk FROM evidence JOIN events ON events.pk = evidence.event_pk
            WHERE events.key = ? ORDER BY evidence.pk LIMIT 1
            """,
            (event_key,),
        ).fetchone()
        return row[0]
    finally:
        conn.close()


def upload(client, event_key, name, data, evidence_type="screenshot", description=""):
    return client.post(
        f"/api/v1/events/{event_key}/evidence",
        data={"type": evidence_type, "description": description},
        files={"file": (name, data, "application/octet-stream")},
    )


# Which files are accepted

@pytest.mark.parametrize(
    "name, data",
    [("a.png", PNG), ("a.jpg", JPEG), ("a.jpeg", JPEG), ("a.gif", GIF), ("a.webp", WEBP), ("a.pdf", PDF),
     ("a.txt", TEXT), ("a.log", TEXT)],
)
def test_accepts_files_whose_content_matches_the_extension(name, data):
    blob = check_file(name, data)
    assert blob.name == name
    assert blob.size == len(data)
    assert blob.sha256 == hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize(
    "name, data",
    [
        ("shot.svg", SVG),  # scripts run inside SVG
        ("page.html", b"<html></html>"),
        ("program.exe", PNG),
        ("no-extension", PNG),
        ("shot.jpg", PNG),  # content does not match the extension
        ("shot.png", JPEG),
        ("notes.txt", b"\x00\x01binary"),
        ("notes.log", b"\xff\xfe not utf-8 \xc3"),
        ("empty.png", b""),
    ],
)
def test_rejects_files_whose_content_does_not_match(name, data):
    with pytest.raises(EvidenceRejected):
        check_file(name, data)


def test_rejects_files_over_the_size_limit():
    with pytest.raises(EvidenceRejected, match="10 MiB"):
        check_file("big.png", PNG + b"0" * MAX_EVIDENCE_BYTES)


def test_name_is_reduced_to_a_plain_file_name():
    blob = check_file("..\\..\\C:/evil/Übersicht\x07.png", PNG)
    assert blob.name == "Übersicht.png"


def test_delivery_headers_block_sniffing_and_scripts():
    headers = delivery_headers("Übersicht.png", "image/png")
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Content-Security-Policy"] == "default-src 'none'; sandbox"
    assert headers["Content-Disposition"].startswith("inline; filename*=UTF-8''")
    assert delivery_headers("report.pdf", "application/pdf")["Content-Disposition"].startswith("attachment")


# Delivery

def test_anonymous_request_gets_no_evidence(db_path):
    client = TestClient(app_for(db_path))
    assert client.get(f"/api/v1/evidence/{evidence_id(db_path, 'siem-alert')}").status_code == 401


def test_viewer_gets_stored_content_with_safe_headers(db_path):
    viewer = api_login(db_path, "viewer")
    response = viewer.get(f"/api/v1/evidence/{evidence_id(db_path, 'siem-alert')}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == (DATA_DIR / "evidence" / "siem-alert.png").read_bytes()
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in response.headers["content-security-policy"]


def test_unknown_evidence_is_not_found(db_path):
    viewer = api_login(db_path, "viewer")
    assert viewer.get("/api/v1/evidence/99999").status_code == 404


def test_viewer_cannot_upload_or_delete(db_path):
    viewer = api_login(db_path, "viewer")
    assert upload(viewer, "exploit", "probe.png", PNG).status_code == 403
    assert viewer.delete(f"/api/v1/evidence/{evidence_id(db_path, 'siem-alert')}").status_code == 403


@pytest.mark.parametrize(
    "name, data, evidence_type",
    [
        ("fake.png", JPEG, "screenshot"),
        ("shot.svg", SVG, "screenshot"),
        ("page.html", b"<html>", "other"),
        ("probe.png", PNG, "unknown-type"),
    ],
)
def test_bad_upload_is_rejected_and_stores_nothing(db_path, name, data, evidence_type):
    anna = api_login(db_path, "anna")
    before = anna.get("/api/v1/events/exploit").json()

    response = upload(anna, "exploit", name, data, evidence_type=evidence_type)

    assert response.status_code == 422
    after = anna.get("/api/v1/events/exploit").json()
    assert after["evidence"] == before["evidence"]


def test_evidence_in_the_event_body_is_rejected(db_path):
    anna = api_login(db_path, "anna")
    response = anna.post(
        "/api/v1/events",
        json={
            "key": "inline-1",
            "t0": "2026-10-15T10:00:00+02:00",
            "phase": "Analysis",
            "actor": "Analyst",
            "action": "Mit Evidence im Body",
            "status": "open",
            "topic": "inc-2026-042",
            "evidence": [{"file": "evidence/siem-alert.png", "type": "screenshot"}],
        },
    )
    assert response.status_code == 422


def test_html_delete_requires_the_csrf_token(db_path):
    anna = api_login(db_path, "anna")
    evidence = evidence_id(db_path, "siem-alert")

    denied = anna.post(
        f"/events/siem-alert/evidence/{evidence}/delete", data={"csrf_token": "falsch"}, follow_redirects=False
    )
    assert denied.status_code == 403

    allowed = anna.post(
        f"/events/siem-alert/evidence/{evidence}/delete",
        data={"csrf_token": anna.headers["X-CSRF-Token"]},
        follow_redirects=False,
    )
    assert allowed.status_code == 303
    assert anna.get("/api/v1/events/siem-alert").json()["evidence"] == []


