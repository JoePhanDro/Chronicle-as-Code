"""One German wording for status and event counts, in the pages, the form, the filters and the dashboard."""

import json
import re

import pytest

from chronicle.models import STATUS_LABELS, Status
from chronicle.pages import TEMPLATES


@pytest.fixture(autouse=True)
def insecure_cookies(monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")


def test_every_status_has_a_german_label_and_the_template_global_matches():
    assert set(STATUS_LABELS) == set(Status)
    assert TEMPLATES.env.globals["status_text"] == {"open": "offen", "in_progress": "in Arbeit", "resolved": "abgeschlossen"}


def test_count_filter_uses_the_singular_for_one():
    count = TEMPLATES.env.filters["count_events"]
    assert (count(0), count(1), count(7)) == ("0 Events", "1 Event", "7 Events")


def test_form_and_list_use_the_same_words_as_the_read_view(api_client):
    client = api_client("analyst")
    form = client.get("/events/exploit").text
    options = re.findall(r'<option value="(open|in_progress|resolved)"[^>]*>([^<]*)</option>', form)
    assert dict(options) == {"open": "offen", "in_progress": "in Arbeit", "resolved": "abgeschlossen"}
    assert "in progress" not in form
    listing = client.get("/events").text
    assert "in progress" not in listing and "in Arbeit" in listing


def test_topic_list_and_status_line_use_the_singular(api_client):
    client = api_client("analyst")
    token = client.headers["X-CSRF-Token"]
    client.post("/api/v1/topics", json={"key": "eins", "title": "Eins", "kind": "project"})
    client.post("/topics/eins/link", data={"event": "feed-cve", "csrf_token": token})
    assert "1 Event<" in client.get("/topics").text and "1 Events" not in client.get("/topics").text
    assert "1 Event</span>" in client.get("/topics/eins").text


def test_dashboard_ships_the_status_words_for_its_script(api_client):
    html = api_client("viewer").get("/topics/inc-2026-042/dashboard").text
    payload = json.loads(re.search(r'<script id="payload"[^>]*>(.*?)</script>', html, re.S).group(1))
    assert payload["statusText"]["in_progress"] == "in Arbeit"
    assert "statusName(ev.status)" in html and "in_progress'.replace" not in html
