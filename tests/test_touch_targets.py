"""Touch targets: one rule in the stylesheet gives controls 44px on narrow screens and touch devices."""

import re
from pathlib import Path

from chronicle.pages import TEMPLATES

ROOT = Path(__file__).resolve().parent.parent
INPUT_CSS = (ROOT / "chronicle" / "static" / "input.css").read_text(encoding="utf-8")


def test_stylesheet_sets_44px_for_controls_on_narrow_and_touch_screens():
    block = INPUT_CSS[INPUT_CSS.index("@media (max-width: 639px), (pointer: coarse)"):]
    for selector in ("button", "select", "summary", "a.rounded-lg", 'label:has(> input[type="checkbox"]'):
        assert selector in block
    assert re.search(r"min-height:\s*44px", block)


def test_isolated_text_links_pad_themselves_on_narrow_screens():
    event = (Path(TEMPLATES.env.loader.searchpath[0]) / "event.html").read_text(encoding="utf-8")
    for link in ("/events?actor=", "/events?system=", 'href="/topics/{{ topic }}"'):
        tag = event[event.index(link):].split(">", 1)[0]
        assert "max-sm:min-h-11" in tag, link
