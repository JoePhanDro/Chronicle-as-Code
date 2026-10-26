"""Dashboard accessibility: reduced motion, table headers, names for the graphics, touch targets."""

import re

import pytest

from chronicle.loader import load_timeline
from chronicle.renderer import render_dashboard
from tests.conftest import DATA_DIR


@pytest.fixture(scope="module")
def html():
    return render_dashboard(load_timeline(DATA_DIR / "timeline.yaml"))


def test_the_pulse_stops_under_reduced_motion_and_keeps_its_colour(html):
    block = re.search(r"@media \(prefers-reduced-motion: reduce\) \{(.*?)\n    \}", html, re.S).group(1)
    assert ".st-in_progress { animation: none; }" in block
    assert "transform: none" in block  # the hover lift of the cards
    assert "@keyframes st-pulse" in html and "@keyframes pulse" not in html  # no clash with Tailwind's pulse


def test_script_animations_go_through_the_reduced_motion_switch(html):
    assert "matchMedia('(prefers-reduced-motion: reduce)')" in html
    # every vis animation is either switched off outright or wrapped in motion(...); st-pulse is the CSS dot
    assert set(re.findall(r"animation: (\S+)", html)) <= {"false", "motion({", "none;", "st-pulse"}


def test_event_table_headers_are_column_headers_and_the_table_has_a_caption(html):
    assert "th.scope = 'col'" in html
    assert "el('caption', 'sr-only'" in html


def test_timeline_and_graph_have_a_name_and_point_to_the_event_table(html):
    for view, label in (("timeline", "Timeline der Ereignisse"), ("graph", "Graph der Zusammenhänge")):
        assert f'id="{view}" role="group" aria-label="{label}"' in html
        assert f'aria-describedby="hint-{view}"' in html
        assert re.search(rf'id="hint-{view}" class="sr-only">[^<]*Tab Ereignisse', html)


def test_touch_targets_reach_44px_on_coarse_pointers_and_narrow_screens(html):
    block = re.search(r"@media \(pointer: coarse\), \(max-width: 639px\) \{(.*?)\n    \}", html, re.S).group(1)
    for selector in (".btn", ".view-tab", "#filters select"):
        assert selector in block
    assert "min-height: 44px" in block
