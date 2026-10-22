import re
import shutil
from pathlib import Path

import pytest
from playwright.sync_api import expect

from chronicle.loader import load_timeline
from chronicle.renderer import render_dashboard

pytestmark = pytest.mark.browser

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
EVENT_TITLE = "Ungewöhnliche Requests auf /api/upload"


@pytest.fixture(scope="session")
def dashboard_url(tmp_path_factory):
    """The static report, rendered from the sample timeline the way CI and the report download do it."""
    folder = tmp_path_factory.mktemp("dashboard")
    html = render_dashboard(load_timeline(DATA_DIR / "timeline.yaml"))
    (folder / "dashboard.html").write_text(html, encoding="utf-8")
    shutil.copy(ROOT / "chronicle" / "static" / "app.css", folder / "app.css")
    shutil.copytree(DATA_DIR / "evidence", folder / "evidence")
    return (folder / "dashboard.html").as_uri()


def wait_for_graph_layout(page):
    """Waits until the graph positions stop changing, so that node coordinates are final."""
    previous = None
    for _ in range(100):
        current = page.evaluate("JSON.stringify(network.getPositions())")
        if current == previous:
            return
        previous = current
        page.wait_for_timeout(100)
    raise AssertionError("graph layout did not settle")


def test_deep_link_opens_the_event(new_page, dashboard_url):
    page = new_page(width=1280, height=900)
    page.goto(f"{dashboard_url}#siem-alert")

    expect(page.locator("#details")).to_contain_text(EVENT_TITLE)


def test_clicking_a_timeline_item_shows_its_details(new_page, dashboard_url):
    page = new_page(width=1280, height=900)
    page.goto(dashboard_url)

    page.locator(".vis-item", has_text="Remote Code Execution").first.click()

    expect(page.locator("#details")).to_contain_text("Remote Code Execution erfolgreich")


def test_hovering_a_graph_node_shows_its_type_and_name(new_page, dashboard_url):
    page = new_page(width=1280, height=900)
    page.goto(dashboard_url)
    wait_for_graph_layout(page)

    canvas = page.locator("#graph canvas").first.bounding_box()
    point = page.evaluate(
        "(id) => network.canvasToDOM(network.getPositions([id])[id])", "event:siem-alert"
    )
    page.mouse.move(canvas["x"] + point["x"], canvas["y"] + point["y"])

    tooltip = page.locator("#graph .vis-tooltip")
    expect(tooltip).to_be_visible()
    expect(tooltip).to_contain_text("Event")
    expect(tooltip).to_contain_text(EVENT_TITLE)


def test_collapsing_the_graph_gives_the_timeline_the_space(new_page, dashboard_url):
    page = new_page(width=1280, height=900)
    page.goto(dashboard_url)

    page.locator("#graph-view .view-toggle").click()

    expect(page.locator("#graph-view")).to_have_class(re.compile(r"\bis-collapsed\b"))
    expect(page.locator("#graph-view .view-toggle")).to_have_attribute("aria-expanded", "false")
    timeline_height = page.evaluate("document.getElementById('timeline-body').clientHeight")
    assert timeline_height > 500


def test_mermaid_block_is_drawn_as_a_diagram(new_page, dashboard_url):
    page = new_page(width=1280, height=900)
    page.goto(f"{dashboard_url}#exploit")

    # Mermaid is loaded from the CDN when the block is shown, which can take a while.
    expect(page.locator("#details .mermaid-diagram svg")).to_be_visible(timeout=60_000)
