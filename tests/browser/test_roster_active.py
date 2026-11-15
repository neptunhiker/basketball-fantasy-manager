"""The Insights switch on the Rosters page, in a real browser.

Screenshots land in tmp/screens/roster-active/."""

import pytest
from django.urls import reverse
from playwright.sync_api import expect

from apps.fantasy.models import Roster

from .conftest import page_overflow, shot

FOLDER = "roster-active"
URL = reverse("fantasy:roster-list")


@pytest.fixture
def two_rosters(game):
    Roster.objects.create(
        manager=game["manager"], season=game["season"], name="What If", is_active=False
    )
    return game


@pytest.mark.parametrize("size", ["phone", "tablet", "desktop"])
@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_the_cards_fit_every_screen(two_rosters, open_page, size, dark):
    page = open_page(two_rosters["user"], URL, size=size, dark=dark)

    assert page_overflow(page) == 0
    expect(page.get_by_role("switch")).to_have_count(2)
    shot(page, FOLDER, f"list-{size}-{'dark' if dark else 'light'}")


def test_the_switch_hides_a_roster_without_a_reload(two_rosters, open_page):
    page = open_page(two_rosters["user"], URL, size="phone")
    roster = two_rosters["roster"]
    card = page.locator(f"#roster-card-{roster.pk}")
    switch = card.get_by_role("switch")
    expect(switch).to_have_attribute("aria-checked", "true")

    switch.click()

    expect(card.get_by_role("switch")).to_have_attribute("aria-checked", "false")
    expect(card.get_by_role("switch")).to_contain_text("Hidden from the points chart")
    expect(page.get_by_text("Bulla Ballers is hidden from Insights.")).to_be_visible()
    roster.refresh_from_db()
    assert roster.is_active is False
