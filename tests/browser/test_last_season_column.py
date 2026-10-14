"""Last season's fantasy points in the players list, in a real browser.
Screenshots land in tmp/screens/last-season-column/."""

from decimal import Decimal

import pytest
from django.urls import reverse
from playwright.sync_api import expect

from ..test_player_season_stats import stats_for
from .conftest import page_overflow, shot

FOLDER = "last-season-column"
LIST = reverse("nba:player-list")


@pytest.fixture
def stored(game):
    """A season for 30 of the game's players; one of them played only 4 games."""
    players = game["players"]
    for index, player in enumerate(players[:30]):
        stats_for(
            player,
            team=player.team.abbreviation,
            game_fantasy_points=Decimal(55) - Decimal(index) * Decimal("1.3"),
            games_played=4 if index == 5 else 60,
        )
    return players


def column_values(page):
    """The FP 25/26 cells top to bottom, as the table shows them."""
    index = page.locator("main thead th").evaluate_all(
        "ths => ths.findIndex(th => th.textContent.includes('FP 25/26'))"
    )
    assert index > 0, "the column is there"
    return page.locator(f"main tbody tr td:nth-child({index + 1})").all_inner_texts()


def first_number(text):
    return Decimal(text.split()[0]) if text.strip() not in {"", "\u2013"} else None


@pytest.mark.parametrize("size", ["phone", "tablet", "desktop"])
@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_the_column_fits_every_screen(game, stored, open_page, size, dark):
    page = open_page(game["user"], LIST + "?sort=last_fp", size=size, dark=dark)

    assert page_overflow(page) == 0
    header = page.get_by_role("link", name="FP 25/26")
    expect(header).to_be_visible()
    values = [first_number(text) for text in column_values(page)]
    present = [value for value in values if value is not None]
    assert present == sorted(present, reverse=True), "high first"
    assert values[len(present) :] == [None] * (len(values) - len(present)), "gaps last"
    shot(page, FOLDER, f"list-{size}-{'dark' if dark else 'light'}")


def test_clicking_the_header_sorts_high_then_low(game, stored, open_page):
    page = open_page(game["user"], LIST, size="desktop")

    page.get_by_role("link", name="FP 25/26").click()
    page.wait_for_url("**sort=last_fp**dir=desc**")
    page.wait_for_load_state("networkidle")
    assert first_number(column_values(page)[0]) == Decimal("55.0")

    page.get_by_role("link", name="FP 25/26").click()
    page.wait_for_url("**dir=asc**")
    page.wait_for_load_state("networkidle")
    lowest = first_number(column_values(page)[0])
    assert lowest == min(
        first_number(text) for text in column_values(page) if first_number(text) is not None
    )


def test_a_short_season_is_shown_quietly_with_its_games(game, stored, open_page):
    page = open_page(game["user"], LIST + "?sort=last_fp", size="tablet")

    short = page.locator("main tbody td", has_text="4 G")
    expect(short).to_be_visible()
    assert "neutral-400" in short.locator("span").first.get_attribute("class")


def test_the_explanation_opens_on_a_phone(game, stored, open_page):
    page = open_page(game["user"], LIST + "?sort=last_fp")

    page.get_by_role("button", name="What is FP 25/26?").click()
    tip = page.get_by_role("tooltip")
    expect(tip).to_be_visible()
    box = tip.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= page.viewport_size["width"]
    shot(page, FOLDER, "explanation-phone")


def test_the_filter_works_from_the_phone_filters(game, stored, open_page):
    page = open_page(game["user"], LIST + "?sort=last_fp")

    page.get_by_role("button", name="Filters").click()
    label = page.locator("label", has_text="At least 20 games in 25/26")
    expect(label).to_be_visible()
    assert label.bounding_box()["height"] >= 40, "a finger-sized row"
    shot(page, FOLDER, "filters-open-phone")
    page.get_by_label("At least 20 games in 25/26").check()
    page.wait_for_url("**last_min_games=1**")
    page.wait_for_load_state("networkidle")

    values = column_values(page)
    assert len(values) == 29, "30 with a line, minus the 4-game season"
    assert all(first_number(text) is not None for text in values)


def test_names_stay_in_view_while_the_figures_scroll(game, stored, open_page):
    page = open_page(game["user"], LIST + "?sort=last_fp")

    page.locator("main .overflow-x-auto").first.evaluate("el => el.scrollLeft = 400")
    name_cell = page.locator("main tbody tr").first.locator("td").nth(1)
    box = name_cell.bounding_box()
    assert 0 <= box["x"] < 80
    shot(page, FOLDER, "list-scrolled-phone")


def test_the_team_page_has_the_column(game, stored, open_page):
    team = stored[0].team.abbreviation.lower()
    page = open_page(game["user"], reverse("nba:team-detail", args=[team]), size="phone")

    expect(page.get_by_role("link", name="FP 25/26")).to_be_visible()
    assert page_overflow(page) == 0
    shot(page, FOLDER, "team-phone")


def test_german_fits_a_phone(game, stored, open_page):
    game["user"].language = "de"
    game["user"].save(update_fields=["language"])

    page = open_page(game["user"], LIST + "?sort=last_fp")

    expect(page.get_by_role("link", name="FP 25/26")).to_be_visible()
    assert "55,0" in column_values(page)[0]
    assert page_overflow(page) == 0
    shot(page, FOLDER, "list-phone-german")
