"""Browser tests: the app runs on a local port and Chromium drives it. Run with `pytest -m browser`."""

import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import sync_playwright

from chronicle import accounts, security
from chronicle.api import create_app
from chronicle.db import connect
from chronicle.importer import import_folder

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
PASSWORD = "correct horse battery"
ADMIN_PASSWORD = "admin-passwort-123"
PASSWORDS = {"chef": ADMIN_PASSWORD, "anna": PASSWORD, "viewer": PASSWORD}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def app_url(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("ui") / "chronicle.db"
    conn = connect(db_path)
    accounts.create_user(conn, "chef", security.hash_password(ADMIN_PASSWORD), "admin")
    accounts.create_user(conn, "anna", security.hash_password(PASSWORD), "analyst")
    accounts.create_user(conn, "viewer", security.hash_password(PASSWORD), "viewer")
    conn.close()
    import_folder(db_path, DATA_DIR)

    # Local HTTP, so the session cookie must not require HTTPS.
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("CHRONICLE_COOKIE_SECURE", "0")
        port = free_port()
        server = uvicorn.Server(uvicorn.Config(create_app(db_path), host="127.0.0.1", port=port, log_level="warning"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 15
        while not server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("test server did not start")
            time.sleep(0.05)
        yield f"http://127.0.0.1:{port}"
        server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def new_page(browser):
    """A page in its own browser context, so that sessions do not leak between tests."""
    contexts = []

    def make(width=1280, height=800):
        context = browser.new_context(viewport={"width": width, "height": height})
        contexts.append(context)
        return context.new_page()

    yield make
    for context in contexts:
        context.close()


@pytest.fixture
def login(new_page, app_url):
    """Logs in through the login form and returns the page on the start page."""

    def make(username):
        page = new_page()
        page.goto(f"{app_url}/login")
        page.fill("input[name=username]", username)
        page.fill("input[name=password]", PASSWORDS[username])
        page.click("button[type=submit]")
        page.wait_for_url(f"{app_url}/")
        return page

    return make
