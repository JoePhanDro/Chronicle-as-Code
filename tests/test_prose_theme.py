"""Markdown text (event details) stays readable in both themes; diagrams follow the theme."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INPUT_CSS = (ROOT / "chronicle" / "static" / "input.css").read_text(encoding="utf-8")

# Tailwind palette values for the shades the prose colours use, and the theme switch of tailwind.config.js:
# light mirrors the scale (50 <-> 950, 300 <-> 700, ...), slate-500 has its own value per theme, white is slate-900.
SLATE = {50: "f8fafc", 100: "f1f5f9", 200: "e2e8f0", 300: "cbd5e1", 400: "94a3b8", 500: "64748b", 600: "475569",
         700: "334155", 800: "1e293b", 900: "0f172a", 950: "020617"}
SKY = {300: "7dd3fc", 400: "38bdf8", 500: "0ea5e9", 600: "0284c7", 700: "0369a1"}
INVERTED = {50: 950, 100: 900, 200: 800, 300: 700, 400: 600, 500: 500, 600: 400, 700: 300, 800: 200, 900: 100, 950: 50}
FAINT = {"dark": "7183a0", "light": "5c6b82"}
PALETTES = {"slate": SLATE, "sky": SKY}

TEXT_ROLES = ["body", "lead", "links", "headings", "bold", "counters", "quotes", "captions", "code", "pre-code"]


def prose_variables() -> dict[str, tuple[str, int | None]]:
    block = re.search(r":root:root \.prose-chronicle \{(.*?)\n\}", INPUT_CSS, re.S).group(1)
    found = {}
    for role, name, shade in re.findall(r"--tw-prose-([\w-]+): rgb\(var\(--(\w+)(?:-(\d+))?\)\);", block):
        found[role] = (name, int(shade) if shade else None)
    return found


def colour(name: str, shade: int | None, theme: str) -> str:
    if name == "white":
        return "ffffff" if theme == "dark" else SLATE[900]
    if name == "slate" and shade == 500:
        return FAINT[theme]
    return PALETTES[name][shade if theme == "dark" else INVERTED[shade]]


def luminance(hex_value: str) -> float:
    channels = [int(hex_value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("role", TEXT_ROLES)
def test_prose_text_has_enough_contrast_on_the_page(role, theme):
    name, shade = prose_variables()[role]
    page = colour("slate", 950, theme)
    assert contrast(colour(name, shade, theme), page) >= 4.5, (role, theme)


def test_templates_do_not_use_the_fixed_light_prose_invert():
    # prose-invert has fixed light colours: unreadable on the light theme
    for template in (ROOT / "chronicle" / "templates").glob("*.html"):
        assert "prose-invert" not in template.read_text(encoding="utf-8"), template.name


def test_event_details_and_dashboard_use_the_themed_prose_class():
    assert 'class="prose prose-chronicle' in (ROOT / "chronicle" / "templates" / "event.html").read_text(encoding="utf-8")
    assert "prose prose-chronicle" in (ROOT / "chronicle" / "templates" / "dashboard.html").read_text(encoding="utf-8")


def test_diagrams_follow_the_interface_theme():
    script = (ROOT / "chronicle" / "static" / "diagrams.js").read_text(encoding="utf-8")
    assert 'theme: "base"' in script and "themeVariables" in script  # the interface's own palette, not mermaid's
    assert 'attributeFilter: ["data-theme"]' in script  # a theme change redraws what is on the page
    assert "document.documentElement.dataset.theme" in script and "prefers-color-scheme: light" in script
