"""Status as a label of its own and a clearer hierarchy on the event, topic and topic list pages."""

import re

import pytest


@pytest.fixture(autouse=True)
def insecure_cookies(monkeypatch):
    monkeypatch.setenv("CHRONICLE_COOKIE_SECURE", "0")


def test_event_header_carries_the_phase_as_an_edge_and_phase_and_status_as_labels(api_client):
    html = api_client("viewer").get("/events/exploit").text
    start = html.index("<header class=\"mb-8 border-l-[3px]")
    header = html[start:html.index("</header>", start)]
    assert 'style="border-color: #ef4444"' in header  # Exploit is red
    assert '<span class="sr-only">Phase: </span>' in header and '<span class="sr-only">Status: </span>' in header
    assert "in Arbeit" in header and "sm:text-2xl" in header
    # phase and status moved out of the fact strip, which keeps time, actor and system
    facts = header[header.index("<dl"):]
    assert "Phase</dt>" not in facts and "Status</dt>" not in facts
    assert all(label in facts for label in ("Zeit (", "Akteur</dt>", "System</dt>"))


def test_description_leads_and_is_the_biggest_heading_below_the_title(api_client):
    html = api_client("viewer").get("/events/exploit").text
    assert html.index(">Beschreibung</h2>") < html.index("Ausgelöst durch")
    assert re.search(r'<h2 class="mb-3 text-base font-semibold text-slate-100">Beschreibung</h2>', html)
    # the secondary section headings are one step louder than the faint grey they had
    assert 'tracking-wide text-slate-400">Evidence</h2>' in html


def test_topic_list_shows_status_on_its_own_line_not_in_the_mono_meta(api_client):
    html = api_client("viewer").get("/topics").text
    assert 'text-xs font-medium text-slate-200"><span class="h-2 w-2' in html
    meta = re.findall(r'<span class="mt-1 block font-mono text-xs text-slate-400">(.*?)</span>', html, re.S)
    assert meta and not any(word in "".join(meta) for word in ("offen", "in Arbeit", "abgeschlossen"))


def test_topic_page_status_is_a_chip(api_client):
    html = api_client("viewer").get("/topics/inc-2026-042").text
    assert re.search(r'rounded-md border border-white/10 px-2\.5 py-1 text-sm font-medium text-slate-100">'
                     r'<span class="h-2 w-2[^>]*></span>in Arbeit', html)
