"""Games left and projected points per player, in a real browser."""

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.nba.models import PlayerInjury

from .conftest import SCREENS, page_overflow, shot
from .test_team_schedule import make_schedule


@pytest.fixture
def projected(game):
    make_schedule(game)
    # Out for ten days with a date to come back on: games come off his count.
    PlayerInjury.objects.create(
        player=game["players"][2],
        observed_at=timezone.now(),
        status="Out",
        injury_type="Ankle",
        return_date=timezone.localdate() + dt.timedelta(days=10),
    )
    return game


@pytest.mark.parametrize("size", ["phone", "tablet", "desktop"])
def test_the_player_list_shows_the_rest_of_the_season(projected, open_page, size):
    page = open_page(projected["user"], reverse("nba:player-list") + "?sort=projected", size=size)

    assert page.get_by_role("link", name="Proj. FP").is_visible() or size == "phone"
    assert page.get_by_text("injured", exact=False).count() >= 1
    assert page_overflow(page) == 0
    shot(page, "rest-of-season", f"players-{size}")


@pytest.mark.parametrize("size", ["phone", "desktop"])
def test_the_player_list_in_dark_mode(projected, open_page, size):
    page = open_page(
        projected["user"], reverse("nba:player-list") + "?sort=projected", size=size, dark=True
    )

    assert page_overflow(page) == 0
    shot(page, "rest-of-season", f"players-{size}-dark")


def test_the_week_filter_keeps_busy_players(projected, open_page):
    page = open_page(projected["user"], reverse("nba:player-list"), size="desktop")

    page.select_option("select[name=week_games]", "4")
    page.wait_for_url("**week_games=4**")
    page.wait_for_load_state("networkidle")
    badges = page.locator("#player-table td span[title*='next 7 days']").all_inner_texts()
    assert badges
    assert all(int(text) >= 4 for text in badges)
    shot(page, "rest-of-season", "players-desktop-week-filter")


@pytest.mark.parametrize("size", ["phone", "tablet"])
def test_the_team_page_table_shows_it_too(projected, open_page, size):
    team = projected["players"][2].team
    page = open_page(
        projected["user"],
        reverse("nba:team-detail", args=[team.abbreviation.lower()]) + "?sort=projected",
        size=size,
    )

    table = page.locator("table").last
    assert page_overflow(page) == 0
    table.screenshot(path=str(SCREENS / "rest-of-season" / f"team-table-{size}.png"))


@pytest.mark.parametrize("size", ["phone", "tablet", "desktop"])
def test_the_player_page_shows_his_rest_of_the_season(projected, open_page, size):
    injured = projected["players"][2]
    page = open_page(
        projected["user"], reverse("nba:player-detail", args=[injured.slug]), size=size
    )

    box = page.locator("section", has=page.get_by_role("heading", name="Rest of the season"))
    assert box.is_visible()
    assert box.get_by_text("missed while injured").is_visible()
    assert page_overflow(page) == 0
    box.screenshot(path=str(SCREENS / "rest-of-season" / f"player-box-{size}.png"))
    shot(page, "rest-of-season", f"player-{size}")


@pytest.mark.parametrize("size", ["phone", "desktop"])
def test_the_player_page_box_in_dark_mode_and_german(projected, open_page, size):
    user = projected["user"]
    user.language = "de"
    user.save(update_fields=["language"])
    healthy = projected["players"][3]
    page = open_page(user, reverse("nba:player-detail", args=[healthy.slug]), size=size, dark=True)

    box = page.locator("section", has=page.get_by_role("heading", name="Rest der Saison"))
    assert box.is_visible()
    assert page_overflow(page) == 0
    box.screenshot(path=str(SCREENS / "rest-of-season" / f"player-box-{size}-dark-de.png"))
