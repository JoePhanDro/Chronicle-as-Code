"""vis-timeline and vis-network ship with the app (chronicle/static/vendor) instead of loading from a CDN."""

import base64
import hashlib
import re
from pathlib import Path

import pytest

VENDOR = Path(__file__).resolve().parent.parent / "chronicle" / "static" / "vendor"
FILES = ["vis-timeline-graph2d.min.js", "vis-timeline-graph2d.min.css", "vis-network.min.js"]


@pytest.fixture(autouse=True)
def insecure_cookies(monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")


def sha384(path: Path) -> str:
    return base64.b64encode(hashlib.sha384(path.read_bytes()).digest()).decode("ascii")


@pytest.mark.parametrize("name", FILES)
def test_vendored_file_matches_the_hash_in_its_readme(name):
    readme = (VENDOR / "README.md").read_text(encoding="utf-8")
    row = next(line for line in readme.splitlines() if f"`{name}`" in line)
    assert sha384(VENDOR / name) in row


def test_licenses_are_shipped_with_the_libraries():
    names = {path.name for path in VENDOR.glob("*LICENSE*")}
    assert {"vis-timeline.LICENSE-MIT.txt", "vis-network.LICENSE-MIT.txt"} <= names


def test_the_app_dashboard_loads_the_libraries_from_its_own_static_files(api_client):
    client = api_client("viewer")
    html = client.get("/topics/inc-2026-042/dashboard").text
    for name in FILES:
        assert f"/static/vendor/{name}" in html
        assert client.get(f"/static/vendor/{name}").status_code == 200
    assert not re.search(r"cdn\.jsdelivr\.net/npm/vis-", html)
