"""WCAG AA for the text of the interface, in both themes (issue #10).

Every visible text is measured against the background it sits on (tests/ui/contrast_probe.py): 4.5:1, or 3:1 for
large text. Gradients, images and the canvases of the timeline and graph are not measured; the data colours there are
checked by hand (DESIGN.md).
"""

import pytest

from tests.ui.contrast_probe import PROBE

pytestmark = pytest.mark.browser

PAGES = [
    "/", "/events", "/events/new", "/events/exploit", "/topics", "/topics/inc-2026-042", "/suche?q=Webshell", "/hilfe",
    "/admin/users", "/admin/audit", "/reports", "/topics/inc-2026-042/dashboard",
]


def failures(page):
    return [f"{item['ratio']} < {item['need']}: {item['tag']} «{item['text']}» ({item['fg']}, {item['size']}px)"
            for item in page.evaluate(PROBE)]


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_text_has_enough_contrast_on_every_page(login, app_url, theme):
    page = login("chef")
    page.evaluate(f"localStorage.setItem('chronicle.theme', '{theme}')")
    found = []
    for path in PAGES:
        page.goto(app_url + path)
        page.wait_for_load_state("networkidle")
        found += [f"{path}: {line}" for line in failures(page)]
    assert not found, "\n".join(found)


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_the_settings_dialog_and_the_login_page_have_enough_contrast(new_page, login, app_url, theme):
    anonymous = new_page()
    anonymous.add_init_script(f"localStorage.setItem('chronicle.theme', '{theme}')")
    anonymous.goto(app_url + "/login")
    assert not failures(anonymous), failures(anonymous)

    page = login("chef")
    page.evaluate(f"localStorage.setItem('chronicle.theme', '{theme}')")
    page.goto(app_url + "/events")
    page.locator("[data-open-settings]:visible").first.click()
    page.locator("dialog[open]").wait_for()
    assert not failures(page), failures(page)


def test_the_probe_finds_a_text_that_is_too_faint(login, app_url):
    page = login("chef")
    page.goto(app_url + "/events")
    page.evaluate("document.querySelector('main').insertAdjacentHTML('afterbegin',"
                  " '<p style=\"color:#777;background:#888\">zu blass</p>')")

    assert any("zu blass" in line for line in failures(page))


def test_the_ink_text_on_the_primary_gradient_button_is_readable_at_both_ends():
    # The button runs from sky-400 to violet-500 with ink text; the gradient itself is not measured above.
    def luminance(hex_colour):
        channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        r, g, b = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    ink = luminance("#020617")
    for end in ("#38bdf8", "#8b5cf6"):
        assert (luminance(end) + 0.05) / (ink + 0.05) >= 4.5, end
