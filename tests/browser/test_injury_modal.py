"""The injury status button opens the report in a modal, loaded on demand."""

import pytest
from django.urls import reverse
from playwright.sync_api import expect

LIST = reverse("nba:player-list")


@pytest.mark.parametrize("size", ["phone", "desktop"])
def test_the_status_button_opens_the_report(game, open_page, size):
    page = open_page(game["user"], LIST, size=size)
    button = page.locator("#player-table").get_by_role("button", name="Injured").first
    button.scroll_into_view_if_needed()

    with page.expect_response(lambda response: "/injury/" in response.url):
        button.click()

    dialog = page.get_by_role("dialog")
    expect(dialog).to_be_visible()
    expect(dialog.get_by_text("Latest injury report")).to_be_visible()

    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
