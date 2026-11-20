"""The six-game rule on the screens: a padlock where the game will not reprice
a player yet, and the first repricing once he has his games."""

import datetime as dt
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from .conftest import SCREENS, page_overflow, shot


@pytest.fixture
def short_of_six(repriced):
    """Two players the updates skipped, next to the ones they repriced.

    Lukas Locked has four games and keeps his $7.5M. Ronan Return had five
    at both updates, so the game kept his $9.0M starting price; with seven
    games now, Sunday's update prices him on his 13 FP/G for the first time.
    Both are on the roster.
    """
    from apps.fantasy import services
    from apps.fantasy.models import RosterPlayer
    from apps.nba.models import Player

    season = repriced["season"]
    updates = season.salary_update_moments(until=timezone.now())
    first, second = updates[-2], updates[-1]
    before_season = season.season_open_at - dt.timedelta(days=1)
    now = timezone.now() + dt.timedelta(minutes=30)
    team = repriced["teams"][0]

    def read(player, when, salary, fp_per_game, games):
        services.record_snapshot(
            player,
            season,
            when,
            salary=Decimal(salary),
            total_fp=Decimal(str(fp_per_game)) * games,
            games_played=games,
            team=team,
            position=player.position,
        )

    locked = Player.objects.create(first_name="Lukas", last_name="Locked", position="G", team=team)
    read(locked, before_season, 7_500_000, 0, 0)
    read(locked, first + dt.timedelta(minutes=15), 7_500_000, 14, 2)
    read(locked, second + dt.timedelta(minutes=15), 7_500_000, 15, 3)
    read(locked, now, 7_500_000, 15.5, 4)

    returning = Player.objects.create(
        first_name="Ronan", last_name="Return", position="F", team=team
    )
    read(returning, before_season, 9_000_000, 0, 0)
    read(returning, first + dt.timedelta(minutes=15), 9_000_000, 12, 2)
    read(returning, second - dt.timedelta(hours=2), 9_000_000, 12.5, 5)
    read(returning, second + dt.timedelta(minutes=15), 9_000_000, 12.5, 5)
    read(returning, now, 9_000_000, 13, 7)

    for player in (locked, returning):
        player.refresh_from_db()
        RosterPlayer.objects.create(roster=repriced["roster"], player=player, added_at=now)
    return {**repriced, "locked": locked, "returning": returning}


def player_url(player):
    return reverse("nba:player-detail", args=[player.slug])


# --- the player page --------------------------------------------------------------


@pytest.mark.parametrize("size", ["phone", "tablet", "desktop"])
def test_a_locked_salary_shows_the_games_he_still_needs(short_of_six, open_page, size):
    page = open_page(short_of_six["user"], player_url(short_of_six["locked"]), size)

    card = page.locator("#salary-outlook")
    assert card.get_by_text("Salary locked").is_visible()
    assert card.get_by_text("Games towards his first update").is_visible()
    meter = card.get_by_role("meter")
    assert meter.get_attribute("aria-valuenow") == "4"
    assert meter.locator("span").count() == 6
    assert card.get_by_text("2 more games and an update can reprice him.").is_visible()
    # Nothing to break down: no "expected + premium = predicted" sum.
    assert card.get_by_text("Predicted salary").count() == 0
    assert page_overflow(page) == 0
    card.scroll_into_view_if_needed()
    card.screenshot(path=str(SCREENS / "salary-lock" / f"player-locked-{size}.png"))


def test_a_locked_salary_in_the_dark(short_of_six, open_page):
    page = open_page(short_of_six["user"], player_url(short_of_six["locked"]), "phone", True)

    card = page.locator("#salary-outlook")
    card.screenshot(path=str(SCREENS / "salary-lock" / "player-locked-phone-dark.png"))


@pytest.mark.parametrize("size", ["phone", "desktop"])
def test_a_first_repricing_carries_no_premium(short_of_six, open_page, size):
    page = open_page(short_of_six["user"], player_url(short_of_six["returning"]), size)

    card = page.locator("#salary-outlook")
    assert card.get_by_text("First repricing").is_visible()
    assert card.get_by_text("none yet").is_visible()
    # From his $9.0M starting price down to what 13 FP/G is worth.
    assert card.get_by_text("Predicted salary").is_visible()
    # Both past updates skipped him, and the note says why.
    assert card.get_by_text("skipped · 2 games").is_visible()
    assert card.get_by_text("skipped · 5 games").is_visible()
    assert card.get_by_text("left out of the average").is_visible()
    assert page_overflow(page) == 0
    card.scroll_into_view_if_needed()
    card.screenshot(path=str(SCREENS / "salary-lock" / f"player-first-repricing-{size}.png"))


def test_the_german_locked_card_fits_a_phone(short_of_six, open_page):
    user = short_of_six["user"]
    user.language = "de"
    user.save(update_fields=["language"])

    page = open_page(user, player_url(short_of_six["locked"]))

    card = page.locator("#salary-outlook")
    assert card.get_by_text("Gehalt gesperrt").is_visible()
    assert page_overflow(page) == 0
    card.screenshot(path=str(SCREENS / "salary-lock" / "player-locked-phone-de.png"))


# --- the lists --------------------------------------------------------------------


@pytest.mark.parametrize("size", ["phone", "tablet", "desktop"])
def test_the_player_list_shows_a_padlock_instead_of_a_change(short_of_six, open_page, size):
    page = open_page(
        short_of_six["user"], reverse("nba:player-list") + "?q=Locked&sort=outlook", size
    )

    shot(page, "salary-lock", f"list-{size}")
    row = page.locator("#player-table tbody tr").filter(has_text="Lukas Locked")
    # In the Next update column, and on a phone under the name as well.
    assert any(chip.is_visible() for chip in row.get_by_text("4/6 games").all())
    assert page_overflow(page) == 0


@pytest.mark.parametrize("size", ["phone", "tablet", "desktop"])
def test_the_roster_shows_both_players(short_of_six, open_page, size):
    url = reverse("fantasy:roster-build", args=[short_of_six["roster"].pk]) + "?sort=outlook"
    page = open_page(short_of_six["user"], url, size)

    # Phones get cards, wider screens the table: one of them shows each figure.
    chips = [chip for chip in page.get_by_text("4/6 games").all() if chip.is_visible()]
    assert len(chips) == 1
    assert page_overflow(page) == 0
    chips[0].scroll_into_view_if_needed()
    shot(page, "salary-lock", f"roster-{size}")
    if size == "phone":
        card = page.locator("#roster-panel li").filter(has_text="Lukas Locked").first
        card.screenshot(path=str(SCREENS / "salary-lock" / "roster-phone-card.png"))
        card = page.locator("#roster-panel li").filter(has_text="Ronan Return").first
        card.screenshot(path=str(SCREENS / "salary-lock" / "roster-phone-card-return.png"))
