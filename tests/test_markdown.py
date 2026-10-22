import json
import re
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from chronicle import accounts, security
from chronicle.api import create_app
from chronicle.db import connect
from chronicle.importer import import_folder
from chronicle.loader import load_timeline
from chronicle.markdown import render_markdown
from chronicle.renderer import render_dashboard

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PASSWORD = "correct horse battery"
HOSTILE = [
    "<script>alert(1)</script>",
    "[klick](javascript:alert(1))",
    "[klick](<javascript:alert(1)>)",
    '<a href="javascript:alert(1)">klick</a>',
    "<img src=x onerror=alert(1)>",
    "<svg onload=alert(1)></svg>",
    "<iframe src=https://evil.example></iframe>",
    '<div onmouseover="alert(1)">hover</div>',
]


def test_common_markdown_is_rendered():
    html = render_markdown("**fett** und *kursiv*\n\n- eins\n- zwei\n\n| a | b |\n|---|---|\n| 1 | 2 |")

    assert "<strong>fett</strong>" in html
    assert "<em>kursiv</em>" in html
    assert "<li>eins</li>" in html
    assert "<table>" in html


def test_single_newlines_become_line_breaks():
    assert "zeile1<br>" in render_markdown("zeile1\nzeile2")


@pytest.mark.parametrize("source", HOSTILE)
def test_hostile_input_keeps_no_active_content(source):
    # Escaped text such as &lt;img onerror=...&gt; is inert; only real tags and attributes matter.
    html = render_markdown(source).lower()

    for tag in ("<script", "<iframe", "<svg", "<img", "<div", "<a "):
        assert tag not in html, f"{tag!r} survived in {html!r}"
    assert not re.search(r"<[^>]*\son[a-z]+\s*=", html), html


def test_images_are_not_rendered():
    assert "<img" not in render_markdown("![bild](https://evil.example/x.png)")


def test_links_get_noopener():
    assert 'rel="noopener noreferrer"' in render_markdown("[ok](https://example.org)")


def write_event_with_details(folder: Path, details: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    timeline = {
        "topic": {"key": "md-test", "title": "Markdown-Test", "kind": "incident"},
        "events": [{
            "key": "md-ereignis",
            "t0": "2026-10-20T10:00:00+02:00",
            "phase": "Identification",
            "actor": "Tester",
            "action": "Markdown-Ereignis",
            "status": "open",
            "details": details,
        }],
    }
    (folder / "timeline.yaml").write_text(yaml.safe_dump(timeline, allow_unicode=True), encoding="utf-8")


DETAILS = "**Wichtig** mit <script>alert('x')</script> und [Link](javascript:alert(1))\n\n- Punkt"


@pytest.fixture
def db_path(tmp_path):
    folder = tmp_path / "data"
    write_event_with_details(folder, DETAILS)
    path = tmp_path / "markdown.db"
    conn = connect(path)
    accounts.create_user(conn, "viewer", security.hash_password(PASSWORD), "viewer")
    conn.close()
    import_folder(path, folder)
    return path


def test_dashboard_gets_the_same_html_as_the_detail_page(db_path, monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")
    html = render_dashboard(load_timeline(db_path.parent / "data" / "timeline.yaml"))

    payload = json.loads(re.search(r'<script id="payload" type="application/json">(.*?)</script>', html, re.S).group(1))
    dashboard_html = payload["events"][0]["details_html"]

    client = TestClient(create_app(db_path))
    client.post("/api/v1/auth/login", json={"username": "viewer", "password": PASSWORD})
    page = client.get("/events/md-ereignis").text

    expected = render_markdown(DETAILS)
    assert dashboard_html == expected
    assert expected in page


def test_detail_page_shows_markdown_without_active_content(db_path, monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")
    client = TestClient(create_app(db_path))
    client.post("/api/v1/auth/login", json={"username": "viewer", "password": PASSWORD})

    page = client.get("/events/md-ereignis").text

    assert "<strong>Wichtig</strong>" in page
    assert "<li>Punkt</li>" in page
    assert "<script>alert" not in page  # the page has its own script in the header menu
    assert 'href="javascript' not in page.lower()


def test_dashboard_no_longer_loads_browser_markdown_libraries():
    html = render_dashboard(load_timeline(DATA_DIR / "timeline.yaml"))

    assert "marked" not in html
    assert "dompurify" not in html.lower()
