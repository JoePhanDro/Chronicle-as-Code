import re

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.browser


def test_wrong_password_is_rejected(new_page, app_url):
    page = new_page()
    page.goto(f"{app_url}/login")
    page.fill("input[name=username]", "anna")
    page.fill("input[name=password]", "falsches-passwort-123")
    page.click("button[type=submit]")

    expect(page.get_by_role("alert")).to_be_visible()
    expect(page).to_have_url(f"{app_url}/login")


def test_analyst_creates_edits_and_deletes_an_event(login, app_url):
    page = login("anna")

    page.goto(f"{app_url}/events/new")
    page.fill("input[name=action]", "UI-Test Ereignis")
    page.fill("input[name=t0_date]", "2026-10-20")
    page.select_option("select[name=t0_hour]", "12")
    page.select_option("select[name=t0_minute]", "00")
    page.select_option("select[name=t0_zone]", "Europe/Berlin")
    page.select_option("select[name=phase]", "Identification")
    page.select_option("select[name=status]", "open")
    page.fill("input[name=actor]", "Browsertest")
    page.select_option("select[name=topic]", "inc-2026-042")
    page.get_by_role("button", name="Anlegen").click()

    # The key is assigned on save, so the event page URL tells us which event was created.
    expect(page.get_by_role("heading", name="UI-Test Ereignis")).to_be_visible()
    key = page.url.rsplit("/", 1)[-1]
    assert page.url == f"{app_url}/events/{key}"

    edit_form = page.locator(f'form[action="/events/{key}/edit"]')
    expect(edit_form.locator("input[name=action]")).to_be_hidden()  # the edit panel starts closed
    page.get_by_role("link", name="Bearbeiten").click()
    expect(edit_form.locator("input[name=action]")).to_be_focused()
    edit_form.locator("input[name=action]").fill("UI-Test Ereignis geändert")
    edit_form.get_by_role("button", name="Speichern").click()
    expect(page.get_by_role("heading", name="UI-Test Ereignis geändert")).to_be_visible()

    page.get_by_role("link", name="Bearbeiten").click()  # saving closes the panel again; delete sits inside it
    page.on("dialog", lambda dialog: dialog.accept())  # the delete button asks for confirmation
    page.get_by_role("button", name="Event löschen").click()

    expect(page).to_have_url(f"{app_url}/events")
    expect(page.get_by_role("link", name="UI-Test Ereignis geändert")).to_have_count(0)


def test_viewer_sees_no_write_actions(login, app_url):
    page = login("viewer")

    page.goto(f"{app_url}/events")
    expect(page.get_by_role("link", name="Neues Event")).to_have_count(0)
    expect(page.locator("button[aria-controls=menu-new]")).to_have_count(0)

    page.goto(f"{app_url}/events/feed-cve")
    expect(page.get_by_role("button", name="Löschen")).to_have_count(0)
    expect(page.locator('form[action$="/edit"]')).to_have_count(0)


def test_header_menu_closes_with_escape_and_returns_focus(login):
    page = login("chef")

    trigger = page.locator("button[aria-controls=menu-admin]")
    trigger.click()
    expect(page.locator("#menu-admin")).to_be_visible()

    page.keyboard.press("Escape")
    expect(page.locator("#menu-admin")).to_be_hidden()
    expect(trigger).to_be_focused()


def test_event_page_draws_its_mermaid_diagram(login, app_url):
    page = login("anna")
    page.goto(f"{app_url}/events/exploit")

    expect(page.locator(".mermaid-diagram svg")).to_be_visible(timeout=60_000)


def test_mermaid_diagram_is_redrawn_in_the_other_theme(login, app_url):
    page = login("anna")
    page.goto(f"{app_url}/events/exploit")
    node = page.locator(".mermaid-diagram svg .node rect, .mermaid-diagram svg .node polygon").first
    expect(node).to_be_visible(timeout=60_000)
    before = node.evaluate("element => getComputedStyle(element).fill")

    page.locator("#theme-toggle").click()
    expect(page.locator("html")).to_have_attribute("data-theme", re.compile("light|dark"))
    page.wait_for_function(
        "before => { const n = document.querySelector('.mermaid-diagram svg .node rect, .mermaid-diagram svg .node polygon');"
        " return n && getComputedStyle(n).fill !== before; }", arg=before, timeout=15_000)
    assert page.locator(".mermaid-diagram").count() == 1


def test_causes_are_picked_from_a_filterable_checklist_and_saved(login, app_url):
    page = login("anna")
    page.goto(f"{app_url}/events/analysis")
    page.get_by_role("link", name="Bearbeiten").click()
    picker = page.locator("[data-cause-picker]")
    expect(picker.locator('input[value="exploit"]')).to_be_checked()

    picker.get_by_label("Events filtern").fill("siem")
    expect(picker.locator("[data-cause-row]:visible")).to_have_count(1)
    picker.locator('input[value="siem-alert"]').check()
    expect(picker.locator("[data-cause-count]")).to_have_text("2 Events ausgewählt")
    picker.get_by_label("Events filtern").press("Enter")  # does not submit the form
    expect(page).to_have_url(f"{app_url}/events/analysis")

    page.locator('form[action="/events/analysis/edit"]').get_by_role("button", name="Speichern").click()
    expect(page.get_by_role("link", name="Ungewöhnliche Requests auf /api/upload")).to_be_visible()

    page.get_by_role("link", name="Bearbeiten").click()  # put the data back for the other tests
    picker = page.locator("[data-cause-picker]")
    picker.locator('input[value="siem-alert"]').uncheck()
    page.locator('form[action="/events/analysis/edit"]').get_by_role("button", name="Speichern").click()
    expect(page.get_by_role("link", name="Ungewöhnliche Requests auf /api/upload")).to_have_count(0)


def test_header_search_finds_events(login):
    page = login("viewer")

    page.get_by_label("Suchen in Ereignissen, Akteuren und Systemen").fill("Ungewöhnliche")
    page.get_by_role("button", name="Suchen").click()

    expect(page).to_have_url(re.compile(r"/suche\?q=Ungew"))
    expect(page.locator("#hits-events + ul > li")).to_have_count(1)
    expect(page.locator("main")).to_contain_text("Ungewöhnliche Requests auf /api/upload")


def test_settings_dialog_closes_with_escape_and_returns_focus(login):
    page = login("anna")
    trigger = page.get_by_role("button", name="Einstellungen")

    trigger.click()
    expect(page.locator("#settings-dialog")).to_be_visible()
    page.keyboard.press("Escape")

    expect(page.locator("#settings-dialog")).to_be_hidden()
    expect(trigger).to_be_focused()


def test_density_setting_is_saved_and_applied_after_reload(login, app_url):
    page = login("anna")
    page.goto(f"{app_url}/events")
    cell = page.locator("tbody td").first
    normal_padding = cell.evaluate("el => getComputedStyle(el).paddingTop")

    page.get_by_role("button", name="Einstellungen").click()
    dialog = page.locator("#settings-dialog")
    dialog.get_by_label("kompakt").check()

    expect(page.locator("html")).to_have_attribute("data-density", "compact")
    assert cell.evaluate("el => getComputedStyle(el).paddingTop") != normal_padding

    page.reload()
    expect(page.locator("html")).to_have_attribute("data-density", "compact")
    page.get_by_role("button", name="Einstellungen").click()
    expect(dialog.get_by_label("kompakt")).to_be_checked()


LOCK_PASSWORD = "sperr-passwort-ui-123"


def test_reading_lock_hides_the_data_until_unlocked(login, new_page, app_url):
    chef = login("chef")
    dialog = chef.locator("#settings-dialog")
    chef.get_by_role("button", name="Einstellungen").click()
    dialog.get_by_label("Sperr-Passwort (mind. 12 Zeichen)").fill(LOCK_PASSWORD)
    dialog.get_by_role("button", name="Sperre einschalten").click()
    expect(chef).to_have_url(f"{app_url}/")

    try:
        visitor = new_page(width=1280, height=900)
        visitor.goto(f"{app_url}/events")
        expect(visitor.get_by_role("heading", name="Anmelden")).to_be_visible()
        expect(visitor.get_by_text("Ansicht entsperren")).to_be_visible()
        expect(visitor.locator("body")).not_to_contain_text("Ungewöhnliche Requests")

        visitor.get_by_label("Sperr-Passwort").fill(LOCK_PASSWORD)
        visitor.get_by_role("button", name="Entsperren").click()
        expect(visitor).to_have_url(f"{app_url}/")
        visitor.goto(f"{app_url}/events")

        expect(visitor.locator("tbody")).to_contain_text("Ungewöhnliche Requests auf /api/upload")
        expect(visitor.get_by_role("link", name="Passwort")).to_have_count(0)
    finally:
        chef.get_by_role("button", name="Einstellungen").click()
        dialog.get_by_role("button", name="Sperre aufheben").click()
        expect(chef).to_have_url(f"{app_url}/")


def test_folding_panels_name_the_action_the_next_click_does(login, app_url):
    page = login("anna")

    page.goto(f"{app_url}/topics/inc-2026-042")
    more = page.locator("details", has=page.get_by_text("Weitere 2 Events", exact=False)).first
    expect(more.get_by_text("Weitere 2 Events anzeigen")).to_be_visible()
    expect(more.get_by_text("Weitere 2 Events ausblenden")).to_be_hidden()
    more.locator("summary").click()
    expect(more.get_by_text("Weitere 2 Events ausblenden")).to_be_visible()
    expect(more.get_by_text("Weitere 2 Events anzeigen")).to_be_hidden()
    more.locator("summary").click()
    expect(more.get_by_text("Weitere 2 Events anzeigen")).to_be_visible()

    panel = page.locator("details#bearbeiten")
    expect(panel.get_by_text("Topic bearbeiten", exact=True)).to_be_visible()
    panel.locator("summary").click()
    expect(panel.get_by_text("Bearbeiten schließen")).to_be_visible()
    expect(panel.get_by_text("Topic bearbeiten", exact=True)).to_be_hidden()

    page.goto(f"{app_url}/events/lessons")
    upload = page.locator("details#evidence-add")
    expect(upload.get_by_text("Evidence hinzufügen", exact=True)).to_be_visible()
    upload.locator("summary").click()
    expect(upload.get_by_text("Evidence hinzufügen schließen")).to_be_visible()
    expect(upload.get_by_text("Evidence hinzufügen", exact=True)).to_be_hidden()


def test_a_taller_view_makes_the_frame_on_the_topic_page_taller(login, app_url):
    page = login("anna")
    page.goto(f"{app_url}/topics/inc-2026-042")
    FRAME = "iframe[title^='Zeitleiste und Zusammenhänge']"
    frame = page.locator(FRAME)
    inside = page.frame_locator(FRAME)
    inside.locator("#panel-grip").wait_for()
    page.wait_for_timeout(500)  # the frame has fitted itself to the content
    before = frame.evaluate("frame => frame.getBoundingClientRect().height")

    inside.locator("#panel-grip").focus()
    for _ in range(5):
        page.keyboard.press("ArrowDown")

    page.wait_for_function(
        "([selector, before]) => document.querySelector(selector).getBoundingClientRect().height >= before + 150",
        arg=[FRAME, before],
    )
