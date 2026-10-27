"""Small fixes from the surface audit: theme on sign-in pages, column headers, key pattern, 404 page, static index."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import app_for
from chronicle.build import STYLESHEET, build_site

TEMPLATES = Path(__file__).resolve().parent.parent / "chronicle" / "templates"
HTML = {"accept": "text/html,application/xhtml+xml"}


@pytest.fixture(autouse=True)
def insecure_cookies(monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")


@pytest.mark.parametrize("name", ["login", "setup"])
def test_sign_in_pages_apply_the_chosen_theme(name):
    text = (TEMPLATES / f"{name}.html").read_text(encoding="utf-8")
    assert "localStorage.getItem('chronicle.theme')" in text and "dataset.theme = theme" in text


@pytest.mark.parametrize("name", ["events", "admin_users", "admin_audit"])
def test_every_column_header_has_a_scope(name):
    text = (TEMPLATES / f"{name}.html").read_text(encoding="utf-8")
    assert "<th " in text and text.count("<th ") == text.count('<th scope="col"')


@pytest.mark.parametrize("name", ["_event_fields"])
def test_key_pattern_escapes_the_hyphen_so_browsers_with_the_v_flag_keep_it(name):
    # "[...._-]" is a syntax error with the v flag, the browser then drops the pattern and the key is not checked
    text = (TEMPLATES / f"{name}.html").read_text(encoding="utf-8")
    assert 'pattern="[A-Za-z0-9][A-Za-z0-9._\\-]{0,99}"' in text
    assert "._-]" not in text


def test_unknown_address_shows_the_404_page_to_a_signed_in_browser(api_client):
    client = api_client("viewer")
    response = client.get("/gibt-es-nicht", headers=HTML)
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "Nicht gefunden" in response.text and "Zurück zu den Ereignissen" in response.text


def test_unknown_address_sends_a_signed_out_browser_to_the_login(tmp_path):
    client = TestClient(app_for(tmp_path / "x.db"))
    response = client.get("/gibt-es-nicht", headers=HTML, follow_redirects=False)
    assert (response.status_code, response.headers["location"]) == (303, "/login")


def test_clients_that_do_not_ask_for_html_and_the_api_keep_json(api_client):
    client = api_client("viewer")
    assert client.get("/gibt-es-nicht").json() == {"detail": "Not Found"}
    assert client.get("/api/v1/nope", headers=HTML).json() == {"detail": "Not Found"}
    assert client.get("/api/v1/topics/gibt-es-nicht", headers=HTML).status_code == 404


def test_static_index_follows_the_system_theme_and_is_marked_as_a_snapshot(tmp_path):
    if not STYLESHEET.is_file():
        pytest.skip("stylesheet not built (npm run build:css)")
    from tests.conftest import DATA_DIR

    build_site(DATA_DIR, tmp_path / "site")
    index = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert 'name="color-scheme" content="light dark"' in index and "color-scheme: light dark" in index
    assert "Berichts-Snapshot · vertraulich · ohne Anmeldung" in index
