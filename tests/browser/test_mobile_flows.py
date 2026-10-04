"""Phase 3 of the mobile plan (my_docs/MOBILE_EXP.md): the key flows on a phone,
checked in a real browser."""

from django.urls import reverse

from .conftest import PHONE, shot


def in_first_screen(locator):
    box = locator.bounding_box()
    return box is not None and box["y"] + box["height"] <= PHONE["height"]


def test_the_first_player_is_on_the_first_screen(game, open_page):
    page = open_page(game["user"], reverse("nba:player-list"))

    assert in_first_screen(page.locator("main tbody tr").first)
    assert not page.locator("#player-filters").is_visible()
    assert not page.locator("#player-chart").is_visible()
    shot(page, "phase3", "players-phone")


def test_filters_and_chart_fold_out_on_a_phone(game, open_page):
    page = open_page(game["user"], reverse("nba:player-list"))

    # Alpine applies x-show on its next tick, so wait for it rather than poll once.
    page.get_by_role("button", name="Filters").click()
    page.locator("#player-position").wait_for(state="visible", timeout=2000)

    page.get_by_role("button", name="Show chart").click()
    page.locator("#player-chart").wait_for(state="visible", timeout=2000)

    # A filter swaps the table; the chart stays open.
    page.select_option("#player-position", "C")
    page.wait_for_url("**position=C**")
    page.wait_for_load_state("networkidle")
    page.locator("#player-chart").wait_for(state="visible", timeout=2000)
    shot(page, "phase3", "players-filters-open-phone")


def test_filters_and_chart_are_open_on_a_tablet(game, open_page):
    page = open_page(game["user"], reverse("nba:player-list"), size="tablet")

    assert page.locator("#player-position").is_visible()
    assert page.locator("#player-chart").is_visible()


def running_season(game):
    season = game["season"]
    season.starts_on = season.starts_on.replace(year=season.starts_on.year - 1)
    season.signings_open_at = season.signings_close_at = None
    season.save()
    game["roster"].__class__.objects.filter(pk=game["roster"].pk).update(trades_available=1)


def test_the_trade_takes_two_steps_on_a_phone(game, open_page):
    running_season(game)
    page = open_page(game["user"], reverse("fantasy:roster-build", args=[game["roster"].pk]))

    page.get_by_role("button", name="Trade", exact=True).first.click()
    page.wait_for_selector("#trade-body")
    assert page.get_by_text("Step 1 of 2").is_visible()
    assert not page.get_by_role("heading", name="Player in").is_visible()

    page.locator("#trade-body section").first.get_by_role("button").first.click()
    page.wait_for_timeout(400)
    assert page.get_by_text("Step 2 of 2").is_visible()
    assert page.get_by_role("heading", name="Player in").is_visible()
    assert page.get_by_role("button", name="Change player out").is_visible()

    execute = page.get_by_role("button", name="Execute trade")
    assert in_first_screen(execute)
    shot(page, "phase3", "trade-step2-phone")


def test_the_dashboard_buttons_are_on_the_first_screen(game, open_page):
    page = open_page(game["user"], reverse("core:dashboard"))

    assert in_first_screen(page.get_by_role("link", name="My roster").first)
    shot(page, "phase3", "home-phone")


def test_the_player_page_back_arrow_and_popovers(game, open_page):
    player = game["players"][0]
    page = open_page(game["user"], reverse("nba:player-detail", args=[player.slug]))

    assert page.get_by_role("link", name="All players").first.is_visible()

    page.get_by_role("button", name="What is Value?").first.click()
    popover = page.get_by_role("tooltip").first
    popover.wait_for(state="visible")
    box = popover.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= PHONE["width"]
    shot(page, "phase3", "player-popover-phone")
