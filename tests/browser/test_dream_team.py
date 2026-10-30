"""The Dream Team page in a real browser, on every screen size and in both
themes. Screenshots land in tmp/screens/dream-team/."""

import datetime as dt

import pytest
from django.urls import reverse
from playwright.sync_api import expect

from apps.analytics.dream_team import refresh
from apps.fantasy.models import PlayerSnapshot

from .conftest import page_overflow, shot

FOLDER = "dream-team"
URL = reverse("analytics:dream-team")


@pytest.fixture
def dreamed(game):
    """The fixture game's Dream Team for its last two weekly snapshots, so the
    page has a team, a previous one, and something that changed between them.
    Signings opened 20 days ago and the oldest snapshot is 21 days old: that
    one holds the draft-day prices."""
    moments = list(
        PlayerSnapshot.objects.filter(season=game["season"])
        .order_by("as_of")
        .values_list("as_of", flat=True)
        .distinct()
    )
    refresh(game["season"], moments[-2])
    game["dream_team"] = refresh(game["season"], moments[-1])
    return game


def mode(dark):
    return "dark" if dark else "light"


@pytest.mark.parametrize("size", ["phone", "tablet", "desktop"])
@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_the_page_fits_every_screen(dreamed, open_page, size, dark):
    page = open_page(dreamed["user"], URL, size=size, dark=dark)

    assert page_overflow(page) == 0
    expect(page.get_by_role("heading", name="The Dream Team")).to_be_visible()
    expect(page.get_by_role("heading", name="The 15 picks")).to_be_visible()
    expect(page.get_by_role("heading", name="Changes since the last import")).to_be_visible()
    # Fifteen players in the table, each linked to his page.
    assert page.locator("main tbody tr td a").count() == 15
    shot(page, FOLDER, f"page-{size}-{mode(dark)}")


@pytest.mark.parametrize("size", ["phone", "tablet", "desktop"])
def test_each_screen_gets_the_chart_drawn_for_it(dreamed, open_page, size):
    page = open_page(dreamed["user"], URL, size=size)
    wide = page.locator("svg:has(#dream-team-wide-label-bg)")
    compact = page.locator("svg:has(#dream-team-compact-label-bg)")

    shown, hidden = (compact, wide) if size == "phone" else (wide, compact)
    expect(shown).to_be_visible()
    expect(hidden).to_be_hidden()
    # Tick labels a reader can actually read: at least 9 CSS pixels tall.
    label_height = shown.locator("text").first.evaluate("el => el.getBoundingClientRect().height")
    assert label_height >= 9


def test_hovering_a_pick_names_him(dreamed, open_page):
    page = open_page(dreamed["user"], URL, size="desktop")
    pick = dreamed["dream_team"].picks.select_related("player").first().player
    name = f"{pick.first_name} {pick.last_name}"

    dot = page.locator(f'svg:has(#dream-team-wide-label-bg) circle[data-label="{name}"]')
    dot.hover()

    readout = page.locator("[aria-live=polite]")
    expect(readout.get_by_role("link", name=name)).to_be_visible()
    expect(readout.get_by_text("Dream Team", exact=True)).to_be_visible()


def test_tapping_a_pick_on_a_phone_names_him(dreamed, open_page):
    page = open_page(dreamed["user"], URL, size="phone")
    pick = dreamed["dream_team"].picks.select_related("player").first().player
    name = f"{pick.first_name} {pick.last_name}"

    dot = page.locator(f'svg:has(#dream-team-compact-label-bg) circle[data-label="{name}"]')
    dot.scroll_into_view_if_needed()
    dot.tap()

    expect(page.locator("[aria-live=polite]").get_by_role("link", name=name)).to_be_visible()
    shot(page, FOLDER, "tapped-phone")


@pytest.mark.parametrize("width", [320, 390, 768, 1024, 1280])
def test_nothing_spills_at_in_between_widths(dreamed, open_page, width):
    page = open_page(dreamed["user"], URL, size="phone" if width < 768 else "desktop")
    page.set_viewport_size({"width": width, "height": 900})

    assert page_overflow(page) == 0
    rows = page.locator("main li, main tbody tr")
    assert rows.evaluate_all("els => els.every(el => el.scrollWidth <= el.clientWidth + 1)")
    shot(page, FOLDER, f"page-{width}")


def test_the_navigation_leads_there(dreamed, open_page):
    page = open_page(dreamed["user"], reverse("core:dashboard"), size="desktop")

    page.get_by_role("navigation", name="Main navigation").get_by_role(
        "link", name="Analytics"
    ).click()

    page.wait_for_url(f"**{URL}")
    expect(page.get_by_role("heading", name="The Dream Team")).to_be_visible()


def test_before_the_first_result_the_page_explains_why(game, open_page):
    page = open_page(game["user"], URL, size="phone")

    assert page_overflow(page) == 0
    expect(page.get_by_text("has not been worked out yet")).to_be_visible()
    shot(page, FOLDER, "empty-phone")


def test_the_fixture_has_two_imports_to_compare(dreamed):
    """Guard for the fixture itself: the two results really are different
    imports, so the change card above had something to compare."""
    teams = list(dreamed["season"].dream_teams.order_by("as_of"))
    assert len(teams) == 2
    assert teams[0].as_of < teams[1].as_of - dt.timedelta(days=1)
