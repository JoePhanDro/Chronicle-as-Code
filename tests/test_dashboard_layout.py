"""Dashboard layout: reading order on narrow screens and cards that do not land on top of each other."""

import re

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
