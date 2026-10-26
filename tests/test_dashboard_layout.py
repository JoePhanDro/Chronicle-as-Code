"""Dashboard layout: reading order on narrow screens and cards that do not land on top of each other."""

import re
from pathlib import Path

import pytest

from chronicle.loader import load_timeline
from chronicle.renderer import render_dashboard
from tests.conftest import DATA_DIR


@pytest.fixture(scope="module")
def html():
    return render_dashboard(load_timeline(DATA_DIR / "timeline.yaml"))


def test_views_come_before_the_details_in_the_page_and_details_move_left_on_large_screens(html):
    assert html.index('role="tablist"') < html.index('id="details"')
    assert re.search(r'id="details" class="[^"]*\blg:order-first\b[^"]*\blg:col-span-2\b', html)


def test_tabs_stay_in_reach_while_the_page_scrolls_on_small_screens(html):
    tablist = re.search(r'<div role="tablist"[^>]*class="([^"]*)"', html).group(1).split()
    assert "sticky" in tablist and "top-0" in tablist and "lg:static" in tablist


def test_a_click_on_an_event_reveals_the_details_on_stacked_layouts(html):
    assert "function revealDetails()" in html
    assert "scrollIntoView({ block: 'nearest', behavior: REDUCED_MOTION.matches ? 'auto' : 'smooth' })" in html
    # timeline, graph, table and evidence gallery pass reveal; opening a page from a link does not
    assert html.count("reveal: true") == 4


def test_the_phase_legend_scrolls_sideways_instead_of_filling_the_screen_on_phones(html):
    legend = re.search(r'<div class="(glass flex [^"]*overflow-x-auto[^"]*)">', html).group(1).split()
    assert {"flex-none", "overflow-x-auto", "sm:flex-wrap", "sm:overflow-visible"} <= set(legend)


def test_the_standard_zoom_is_part_of_the_timeline_options_not_set_afterwards():
    # moving the window after the first draw left cards stacked on top of each other; "local" keeps the
    # embedded vis bundle out of the text
    html = render_dashboard(load_timeline(DATA_DIR / "timeline.yaml"), vendor="local")
    assert "...zoomWindow," in html
    assert "timeline.setWindow(" not in html and "applyDefaultZoom" not in html


def test_card_classes_survive_vis_timelines_html_filter(html):
    # without the allow-list the filter drops every class, the cards fall back to the width of their text
    # and the stacking is computed from those widths
    assert "xss: { filterOptions: { whiteList: { div: ['class'], span: ['class'] } } }" in html


def test_blur_is_only_on_the_graph_legend_and_nothing_glows():
    # the template source, not the rendered page: the page also holds the stylesheet and the vis bundle
    html = (Path(__file__).resolve().parent.parent / "chronicle" / "templates" / "dashboard.html").read_text(encoding="utf-8")
    assert len(re.findall(r"(?<!-webkit-)backdrop-filter", html)) == 1  # inside .blur-overlay
    assert re.search(r'class="glass blur-overlay absolute bottom-4 left-4', html)
    assert not re.search(r"\.glass \{[^}]*backdrop-filter", html)
    # no zero-offset coloured halos and no sky glow on the logo tile
    assert "box-shadow: 0 0 10px" not in html and "shadow-sky-500" not in html
    assert "0 0 18px" not in html and "0 0 26px" not in html and "0 10px 28px -14px var(--c)" not in html
    assert "box-shadow: 0 0 0 2px var(--c)" in html  # selection is a ring in the phase colour
    assert ".vis-active { box-shadow: none; }" in html  # the library's own sky glow is switched off
