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
    """Waits until positions, scale and view stop changing, so that node coordinates are final."""
    previous, stable = None, 0
    for _ in range(100):
        current = page.evaluate(
            "JSON.stringify([network.getPositions(), network.getScale(), network.getViewPosition()])"
        )
        stable = stable + 1 if current == previous else 0
        if stable >= 5:  # five equal samples in a row, about half a second
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
    page.get_by_role("tab", name="Graph").click()
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


@pytest.mark.parametrize("size", [(1280, 900), (800, 700)])
def test_the_whole_graph_is_in_view_and_fills_it_after_it_settles(new_page, dashboard_url, size):
    # The graph settles while its tab is hidden. It must still end up fitted when the tab opens (issue #211).
    page = new_page(width=size[0], height=size[1])
    page.goto(dashboard_url)
    page.get_by_role("tab", name="Graph").click()
    wait_for_graph_layout(page)

    canvas = page.locator("#graph canvas").first.bounding_box()
    points = page.evaluate(
        "Object.values(network.getPositions()).map(position => network.canvasToDOM(position))"
    )
    xs, ys = [point["x"] for point in points], [point["y"] for point in points]
    assert min(xs) >= 0 and max(xs) <= canvas["width"] and min(ys) >= 0 and max(ys) <= canvas["height"]
    # fitted: the nodes span a good part of the canvas, not a corner of it
    assert max(xs) - min(xs) > 0.5 * canvas["width"] or max(ys) - min(ys) > 0.5 * canvas["height"]


def panel_height(page):
    return page.locator("#panel-graph").evaluate("panel => panel.getBoundingClientRect().height")


def test_the_height_of_the_views_can_be_dragged_and_is_remembered(new_page, dashboard_url):
    page = new_page(width=800, height=900)
    page.goto(dashboard_url)
    page.get_by_role("tab", name="Graph").click()
    before = panel_height(page)
    grip = page.locator("#panel-grip")
    grip.scroll_into_view_if_needed()
    box = grip.bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2

    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x, y + 120, steps=6)
    page.mouse.up()

    assert abs(panel_height(page) - (before + 120)) <= 2
    page.reload()
    page.get_by_role("tab", name="Graph").click()
    assert abs(panel_height(page) - (before + 120)) <= 2

    page.locator("#panel-grip").dblclick()
    assert abs(panel_height(page) - before) <= 2


def test_the_height_of_the_views_can_be_changed_with_the_keyboard(new_page, dashboard_url):
    page = new_page(width=800, height=900)
    page.goto(dashboard_url)
    page.get_by_role("tab", name="Graph").click()
    before = panel_height(page)

    page.locator("#panel-grip").focus()
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowDown")
    assert abs(panel_height(page) - (before + 80)) <= 2
    assert page.locator("#panel-grip").get_attribute("aria-valuenow") == str(round(before + 80))

    page.keyboard.press("Home")
    assert abs(panel_height(page) - before) <= 2


def test_there_is_no_grip_where_the_views_fill_the_window(new_page, dashboard_url):
    page = new_page(width=1280, height=900)
    page.goto(dashboard_url)

    expect(page.locator("#panel-grip")).to_be_hidden()


def node_in_page(page, node_id):
    """Where a graph node is on the page, in pixels."""
    canvas = page.locator("#graph canvas").first.bounding_box()
    point = page.evaluate("(id) => network.canvasToDOM(network.getPositions([id])[id])", node_id)
    return canvas["x"] + point["x"], canvas["y"] + point["y"]


def drag_node(page, node_id, dx, dy):
    x, y = node_in_page(page, node_id)
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + dx / 2, y + dy / 2, steps=5)
    page.mouse.move(x + dx, y + dy, steps=5)
    page.mouse.up()


def test_a_dragged_graph_node_stays_where_it_is_put_also_after_a_reload(new_page, dashboard_url):
    page = new_page(width=1280, height=900)
    page.goto(dashboard_url)
    page.get_by_role("tab", name="Graph").click()
    wait_for_graph_layout(page)
    node = "event:siem-alert"

    drag_node(page, node, 120, 60)
    placed = page.evaluate("(id) => network.getPositions([id])[id]", node)
    page.wait_for_timeout(800)  # the physics would pull it back, were it not pinned

    assert page.evaluate("(id) => network.getPositions([id])[id]", node) == placed
    page.reload()
    page.get_by_role("tab", name="Graph").click()
    wait_for_graph_layout(page)
    assert page.evaluate("(id) => network.getPositions([id])[id]", node) == placed


def test_a_click_on_a_graph_node_does_not_pin_it(new_page, dashboard_url):
    page = new_page(width=1280, height=900)
    page.goto(dashboard_url)
    page.get_by_role("tab", name="Graph").click()
    wait_for_graph_layout(page)

    x, y = node_in_page(page, "event:siem-alert")
    page.mouse.click(x, y)

    assert page.evaluate("graphNodes.get('event:siem-alert').fixed") in (None, False)
    assert page.evaluate("Object.keys(pinned).length") == 0


def test_resetting_the_arrangement_frees_the_nodes_again(new_page, dashboard_url):
    page = new_page(width=1280, height=900)
    page.goto(dashboard_url)
    page.get_by_role("tab", name="Graph").click()
    wait_for_graph_layout(page)
    drag_node(page, "event:siem-alert", 120, 60)
    assert page.evaluate("Object.keys(pinned).length") == 1

    page.get_by_role("button", name="Anordnung zurücksetzen").click()

    assert page.evaluate("Object.keys(pinned).length") == 0
    assert page.evaluate("graphNodes.get().every(node => !node.fixed)")
    page.reload()
    page.get_by_role("tab", name="Graph").click()
    assert page.evaluate("Object.keys(pinned).length") == 0


def test_the_details_link_the_techniques_to_mitre_and_the_header_counts_them(new_page, tmp_path):
    timeline = load_timeline(DATA_DIR / "timeline.yaml")
    events = [event.model_copy(update={"techniques": ["T1190", "T1059.001"]}) if event.key == "siem-alert" else event
              for event in timeline.events]
    timeline = timeline.model_copy(update={"events": events})
    (tmp_path / "dashboard.html").write_text(render_dashboard(timeline), encoding="utf-8")
    shutil.copy(ROOT / "chronicle" / "static" / "app.css", tmp_path / "app.css")
    page = new_page(width=1280, height=900)

    page.goto(f"{(tmp_path / 'dashboard.html').as_uri()}#siem-alert")

    link = page.locator("#details").get_by_role("link", name="T1059.001")
    expect(link).to_have_attribute("href", "https://attack.mitre.org/techniques/T1059/001/")
    expect(link).to_have_attribute("rel", "noopener noreferrer")
    expect(page.locator("#kpis")).to_contain_text("Techniken2")


def test_the_header_has_no_technique_count_without_techniques(new_page, dashboard_url):
    page = new_page(width=1280, height=900)
    page.goto(dashboard_url)

    expect(page.locator("#kpis")).not_to_contain_text("Techniken")


def test_tabs_switch_views_and_keep_the_selection(new_page, dashboard_url):
    page = new_page(width=1280, height=900)
    page.goto(f"{dashboard_url}#/timeline/siem-alert")
    expect(page.get_by_role("tab", name="Timeline")).to_have_attribute("aria-selected", "true")

    page.get_by_role("tab", name="Graph").click()

    expect(page.get_by_role("tab", name="Graph")).to_have_attribute("aria-selected", "true")
    expect(page.locator("#panel-timeline")).to_be_hidden()
    expect(page.locator("#panel-graph")).to_be_visible()
    expect(page).to_have_url(re.compile(r"#/graph/siem-alert$"))
    expect(page.locator("#details")).to_contain_text(EVENT_TITLE)


def test_arrow_keys_move_between_tabs(new_page, dashboard_url):
    page = new_page(width=1280, height=900)
    page.goto(dashboard_url)
    page.get_by_role("tab", name="Timeline").focus()

    page.keyboard.press("ArrowRight")
    expect(page.get_by_role("tab", name="Graph")).to_be_focused()
    expect(page.locator("#panel-graph")).to_be_visible()

    page.keyboard.press("End")
    expect(page.get_by_role("tab", name="Evidence")).to_be_focused()
    expect(page.locator("#panel-evidence")).to_be_visible()


def test_event_table_selects_the_event_and_keeps_the_view(new_page, dashboard_url):
    page = new_page(width=1280, height=900)
    page.goto(dashboard_url)
    page.get_by_role("tab", name="Ereignisse").click()

    page.locator("#panel-events").get_by_role("button", name="Remote Code Execution erfolgreich").click()

    expect(page.locator("#details")).to_contain_text("Remote Code Execution erfolgreich")
    expect(page.get_by_role("tab", name="Ereignisse")).to_have_attribute("aria-selected", "true")
    expect(page).to_have_url(re.compile(r"#/events/exploit$"))


def test_evidence_gallery_lists_the_evidence(new_page, dashboard_url):
    page = new_page(width=1280, height=900)
    page.goto(dashboard_url)
    page.get_by_role("tab", name="Evidence").click()

    expect(page.locator("#panel-evidence button").first).to_be_visible()
    page.locator("#panel-evidence button").first.click()

    expect(page.locator("#details")).to_contain_text("SIEM-Korrelationsregel")


def test_mermaid_block_is_drawn_as_a_diagram(new_page, dashboard_url):
    page = new_page(width=1280, height=900)
    page.goto(f"{dashboard_url}#exploit")

    # Mermaid is loaded from the CDN when the block is shown, which can take a while.
    expect(page.locator("#details .mermaid-diagram svg")).to_be_visible(timeout=60_000)


TOTAL_EVENTS = len(load_timeline(DATA_DIR / "timeline.yaml").events)


def test_phase_filter_applies_to_every_view(new_page, dashboard_url):
    page = new_page(width=1280, height=900)
    page.goto(dashboard_url)
    filters = page.locator("#filters")

    filters.get_by_label("Phase").select_option("Identification")

    expect(filters).to_contain_text(f"2 von {TOTAL_EVENTS} Events")
    expect(page.locator(".vis-item.vis-box")).to_have_count(2)
    expect(page.locator("#panel-events tbody tr:not([hidden])")).to_have_count(2)
    # Only the siem-alert evidence belongs to an identification event.
    expect(page.locator("#panel-evidence button:not([hidden])")).to_have_count(1)


def test_filters_combine_and_reset(new_page, dashboard_url):
    page = new_page(width=1280, height=900)
    page.goto(dashboard_url)
    filters = page.locator("#filters")

    filters.get_by_label("Akteur").select_option("CSIRT Analyst A")
    filters.get_by_label("Status").select_option("resolved")

    expect(filters).to_contain_text(f"1 von {TOTAL_EVENTS} Events")
    expect(page.locator("#panel-events tbody tr:not([hidden])")).to_have_count(1)
    expect(page.locator(".vis-item.vis-box")).to_have_count(1)

    filters.get_by_role("button", name="Filter zurücksetzen").click()

    expect(filters).to_contain_text(f"{TOTAL_EVENTS} Events")
    expect(filters.get_by_label("Akteur")).to_have_value("")
    expect(page.locator("#panel-events tbody tr:not([hidden])")).to_have_count(TOTAL_EVENTS)


def test_system_filter_limits_the_timeline(new_page, dashboard_url):
    page = new_page(width=1280, height=900)
    page.goto(dashboard_url)

    page.locator("#filters").get_by_label("System").select_option("OSINT")

    expect(page.locator(".vis-item.vis-box")).to_have_count(1)
    expect(page.locator(".vis-item.vis-box")).to_contain_text("CVE")


def test_graph_node_kinds_can_be_hidden(new_page, dashboard_url):
    page = new_page(width=1280, height=900)
    page.goto(dashboard_url)
    page.get_by_role("tab", name="Graph").click()
    wait_for_graph_layout(page)

    page.locator("#panel-graph input[data-kind=actor]").uncheck()

    shown = page.evaluate("graphNodes.get().filter(node => !node.hidden).map(node => node.id.split(':')[0])")
    assert "actor" not in shown
    assert shown.count("event") == TOTAL_EVENTS
    assert "system" in shown


def test_filtered_out_event_stays_in_the_details(new_page, dashboard_url):
    page = new_page(width=1280, height=900)
    page.goto(f"{dashboard_url}#/timeline/siem-alert")
    expect(page.locator("#details")).to_contain_text(EVENT_TITLE)

    page.locator("#filters").get_by_label("Phase").select_option("Exploit")

    expect(page.locator("#filters")).to_contain_text(f"1 von {TOTAL_EVENTS} Events")
    expect(page.locator("#details")).to_contain_text(EVENT_TITLE)
    expect(page.locator(".vis-item.vis-box")).to_have_count(1)


def test_evidence_gallery_says_when_nothing_matches(new_page, dashboard_url):
    page = new_page(width=1280, height=900)
    page.goto(dashboard_url)

    page.locator("#filters").get_by_label("Phase").select_option("Exploit")

    expect(page.locator("#panel-evidence")).to_contain_text("Keine Evidence passt zu den Filtern.")
    expect(page.locator("#panel-evidence button:not([hidden])")).to_have_count(0)
