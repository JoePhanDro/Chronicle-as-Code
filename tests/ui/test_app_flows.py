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
    edit_form.locator("input[name=action]").fill("UI-Test Ereignis geändert")
    edit_form.get_by_role("button", name="Speichern").click()
    expect(page.get_by_role("heading", name="UI-Test Ereignis geändert")).to_be_visible()

    page.on("dialog", lambda dialog: dialog.accept())  # the delete button asks for confirmation
    page.get_by_role("button", name="Löschen").click()

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
