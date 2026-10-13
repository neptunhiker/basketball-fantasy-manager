"""The season stats card on the player page in a real browser. Screenshots land
in tmp/screens/player-season-stats/."""

import re
from decimal import Decimal

import pytest
from django.urls import reverse
from playwright.sync_api import expect

from ..test_player_season_stats import stats_for
from .conftest import SCREENS, page_overflow, shot

FOLDER = "player-season-stats"


@pytest.fixture
def stored(game):
    """A season for most of the game's players, so ranks mean something."""
    players = game["players"]
    for index, player in enumerate(players[1:30], start=1):
        stats_for(
            player,
            team=player.team.abbreviation,
            points=Decimal(30) - Decimal(index) / 2,
            fantasy_points=Decimal(55) - Decimal(index),
        )
    return players


def detail(player):
    return reverse("nba:player-detail", args=[player.slug]) + "#season-stats"


def open_card(open_page, user, player, **kwargs):
    page = open_page(user, detail(player), **kwargs)
    page.locator("#season-stats").scroll_into_view_if_needed()
    return page


@pytest.mark.parametrize("size", ["phone", "tablet", "desktop"])
@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_the_card_fits_every_screen(game, stored, open_page, size, dark):
    luka = next(p for p in stored if p.last_name == "Dončić")
    luka.season_stats.update(team_abbreviation="DAL")

    page = open_card(open_page, game["user"], luka, size=size, dark=dark)

    card = page.locator("#season-stats")
    assert card.is_visible()
    assert page_overflow(page) == 0
    assert card.get_by_text("Season 2025-26").is_visible()
    # The four headline numbers: two per row on a phone, one row from `sm`.
    tiles = card.locator("dl").first.locator(":scope > div")
    first, second, third = (tiles.nth(i).bounding_box() for i in range(3))
    assert first["y"] == second["y"]
    if size == "phone":
        assert third["y"] > first["y"]
    else:
        assert third["y"] == first["y"]
    card.screenshot(path=_path(f"card-{size}-{'dark' if dark else 'light'}"))
    shot(page, FOLDER, f"page-{size}-{'dark' if dark else 'light'}")


def test_the_fantasy_points_explanation_opens_on_a_phone(game, stored, open_page):
    page = open_card(open_page, game["user"], stored[1])

    card = page.locator("#season-stats")
    card.get_by_role("button", name="What is Fantasy points?").click()
    tip = card.get_by_role("tooltip")
    expect(tip).to_be_visible()
    box = tip.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= page.viewport_size["width"]
    shot(page, FOLDER, "fp-help-phone")


def test_few_games_and_missing_stats_read_well(game, stored, open_page):
    stats_for(game["players"][0], games_played=4)
    page = open_card(open_page, game["user"], game["players"][0])
    assert page.get_by_text("Only 4 games played").is_visible()
    page.locator("#season-stats").screenshot(path=_path("card-few-games-phone"))

    page = open_card(open_page, game["user"], game["players"][35])
    assert page.get_by_text("No official stats for 2025-26").is_visible()
    assert page_overflow(page) == 0
    page.locator("#season-stats").screenshot(path=_path("card-missing-phone"))


def test_the_german_card_fits_a_phone(game, stored, open_page):
    game["user"].language = "de"
    game["user"].save(update_fields=["language"])

    page = open_card(open_page, game["user"], stored[1])

    assert page.get_by_text("Saison 2025-26").is_visible()
    assert page_overflow(page) == 0
    page.locator("#season-stats").screenshot(path=_path("card-phone-german"))


def _path(name):
    target = SCREENS / FOLDER
    target.mkdir(parents=True, exist_ok=True)
    return str(target / f"{name}.png")


@pytest.mark.parametrize("size", ["phone", "desktop"])
def test_the_card_starts_collapsed_and_opens_on_a_tap(game, stored, open_page, size):
    player = stored[1]
    page = open_page(game["user"], reverse("nba:player-detail", args=[player.slug]), size=size)
    card = page.locator("#season-stats")
    card.scroll_into_view_if_needed()

    assert card.get_by_text("Season 2025-26").is_visible()
    headline = card.get_by_text(re.compile(r"^\d+\.\d FP$"))
    assert headline.is_visible(), "the headline numbers show closed"
    assert not card.get_by_text("Shooting").is_visible()
    summary = card.locator("summary")
    assert summary.bounding_box()["height"] >= 44
    assert page_overflow(page) == 0
    card.screenshot(path=_path(f"card-collapsed-{size}"))

    summary.click()
    expect(card.get_by_text("Shooting")).to_be_visible()
    assert not headline.is_visible()
    summary.click()
    expect(card.get_by_text("Shooting")).to_be_hidden()
