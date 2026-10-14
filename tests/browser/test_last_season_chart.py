"""The salary chart's season toggle in a real browser. Screenshots land in
tmp/screens/last-season-chart/."""

from decimal import Decimal

import pytest
from django.urls import reverse
from playwright.sync_api import expect

from apps.nba.models import Player

from ..test_player_season_stats import stats_for
from .conftest import SCREENS, page_overflow, shot

FOLDER = "last-season-chart"
LIST = reverse("nba:player-list")


@pytest.fixture
def stored(game):
    """A season for 30 of the game's players; one of them played only 4 games."""
    players = game["players"]
    for index, player in enumerate(players[:30]):
        stats_for(
            player,
            team=player.team.abbreviation,
            game_fantasy_points=Decimal(42) - Decimal(index) * Decimal("0.9"),
            games_played=4 if index == 5 else 60,
        )
    return players


def open_chart(page):
    """On a phone the chart sits behind "Show chart"; from `md` it is open."""
    toggle = page.get_by_role("button", name="Show chart")
    if toggle.is_visible():
        toggle.click()
    chart = page.locator("#player-chart")
    expect(chart).to_be_visible()
    return chart


def season_button(page, label):
    return page.get_by_role("group", name="Season shown in the chart").get_by_role(
        "button", name=label
    )


@pytest.mark.parametrize("size", ["phone", "tablet", "desktop"])
@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_switching_to_last_season(game, stored, open_page, size, dark):
    page = open_page(game["user"], LIST, size=size, dark=dark)
    chart = open_chart(page)

    # The game is under way, so the chart opens on this season.
    expect(chart.get_by_text("Points per game and salary")).to_be_visible()
    last_button = season_button(page, "25/26")
    if size == "phone":
        assert last_button.bounding_box()["height"] >= 40
    last_button.click()

    expect(chart.get_by_text("Fantasy points 25/26 and salary")).to_be_visible()
    expect(chart.get_by_text("Points per game and salary")).to_be_hidden()
    assert last_button.get_attribute("aria-pressed") == "true"
    last_chart = chart.locator("svg").nth(1)
    expect(last_chart).to_be_visible()
    assert last_chart.locator("circle.chart-hit").count() == 30

    last_chart.locator("circle.chart-hit").first.focus()
    details = chart.locator("aside").nth(1)
    expect(details.get_by_text("Fantasy points per game 25/26")).to_be_visible()
    assert details.get_by_text("Hotness").count() == 0
    assert page_overflow(page) == 0
    chart.screenshot(path=_path(f"chart-{size}-{'dark' if dark else 'light'}"))


def test_the_choice_survives_filtering(game, stored, open_page):
    page = open_page(game["user"], LIST, size="desktop")
    chart = open_chart(page)
    season_button(page, "25/26").click()

    with page.expect_response(lambda response: "q=a" in response.url):
        page.fill("#player-search", "a")
    page.wait_for_load_state("networkidle")

    expect(page.get_by_text("Fantasy points 25/26 and salary")).to_be_visible()
    assert season_button(page, "25/26").get_attribute("aria-pressed") == "true"
    expect(chart).to_be_visible()


def test_before_tip_off_it_opens_on_last_season(game, stored, open_page):
    Player.objects.update(current_games_played=0)

    page = open_page(game["user"], LIST, size="tablet")
    chart = open_chart(page)

    expect(chart.get_by_text("Fantasy points 25/26 and salary")).to_be_visible()
    assert season_button(page, "25/26").get_attribute("aria-pressed") == "true"
    chart.screenshot(path=_path("chart-before-tip-off-tablet"))


def test_the_team_page_has_the_toggle(game, stored, open_page):
    team = stored[0].team.abbreviation.lower()
    page = open_page(game["user"], reverse("nba:team-detail", args=[team]), size="phone")
    open_chart(page)

    season_button(page, "25/26").click()
    expect(page.get_by_text("Fantasy points 25/26 and salary")).to_be_visible()
    assert page_overflow(page) == 0
    shot(page, FOLDER, "team-phone")


def test_german_fits_a_phone(game, stored, open_page):
    game["user"].language = "de"
    game["user"].save(update_fields=["language"])
    page = open_page(game["user"], LIST)

    page.get_by_role("button", name="Diagramm anzeigen").click()
    page.get_by_role("group", name="Im Diagramm gezeigte Saison").get_by_role(
        "button", name="25/26"
    ).click()

    expect(page.get_by_text("Fantasy-Punkte 25/26 und Gehalt")).to_be_visible()
    assert page_overflow(page) == 0
    page.locator("#player-chart").screenshot(path=_path("chart-phone-german"))


def _path(name):
    target = SCREENS / FOLDER
    target.mkdir(parents=True, exist_ok=True)
    return str(target / f"{name}.png")
