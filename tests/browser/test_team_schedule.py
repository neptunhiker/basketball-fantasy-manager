"""Played and outstanding games on the team pages, in a real browser."""

import datetime as dt
import random

import pytest
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from apps.nba.models import Game

from .conftest import SCREENS, page_overflow, shot


def make_schedule(game, days_back=40, days_ahead=60):
    """A plausible stretch of season around now: every team plays three or
    four times a week, in the evening, American time -- the night in Berlin."""
    teams = game["teams"]
    rng = random.Random(7)
    now = timezone.now().replace(minute=0, second=0, microsecond=0)
    season_year = game["season"].ends_on.year
    number = 0
    for day in range(-days_back, days_ahead):
        playing = rng.sample(teams, k=rng.choice([10, 12, 14, 16]))
        for home, away in zip(playing[::2], playing[1::2], strict=True):
            number += 1
            Game.objects.create(
                espn_id=str(400000000 + number),
                season_year=season_year,
                tip_off=now + dt.timedelta(days=day, hours=rng.choice([1, 2, 3])),
                home_team=home,
                away_team=away,
                status="STATUS_SCHEDULED",
                note="NBA Cup - Group Play" if rng.random() < 0.1 else "",
            )
    return game


@pytest.fixture
def scheduled(game):
    return make_schedule(game)


@pytest.mark.parametrize("size", ["phone", "tablet", "desktop"])
def test_the_team_list_shows_games_left_at_every_width(scheduled, open_page, size):
    page = open_page(scheduled["user"], reverse("nba:team-list"), size=size)

    assert page.get_by_role("progressbar").first.is_visible()
    assert page_overflow(page) == 0
    shot(page, "team-schedule", f"teams-{size}")


@pytest.mark.parametrize("size", ["phone", "desktop"])
def test_the_team_list_in_dark_mode(scheduled, open_page, size):
    page = open_page(scheduled["user"], reverse("nba:team-list"), size=size, dark=True)

    assert page_overflow(page) == 0
    shot(page, "team-schedule", f"teams-{size}-dark")


def test_the_team_list_ranks_by_games_this_week(scheduled, open_page):
    page = open_page(scheduled["user"], reverse("nba:team-list") + "?sort=next", size="tablet")

    badges = page.locator("tbody td:nth-child(8) span[title]").all_inner_texts()
    counts = [int(text) for text in badges]
    assert counts == sorted(counts, reverse=True)
    assert page_overflow(page) == 0
    shot(page, "team-schedule", "teams-tablet-by-week")


def team_url(team):
    return reverse("nba:team-detail", args=[team.abbreviation.lower()])


@pytest.mark.parametrize("size", ["phone", "tablet", "desktop"])
def test_the_team_page_shows_its_schedule_at_every_width(scheduled, open_page, size):
    team = scheduled["teams"][0]
    page = open_page(scheduled["user"], team_url(team), size=size)

    box = page.locator("#schedule")
    assert box.get_by_role("heading", name="Schedule").is_visible()
    assert box.get_by_role("heading", name="Coming up").is_visible()
    assert box.locator("li:visible").count() == 10
    assert page_overflow(page) == 0
    box.screenshot(path=str(SCREENS / "team-schedule" / f"team-box-{size}.png"))
    shot(page, "team-schedule", f"team-{size}")


@pytest.mark.parametrize("size", ["phone", "desktop"])
def test_the_team_page_in_dark_mode(scheduled, open_page, size):
    team = scheduled["teams"][0]
    page = open_page(scheduled["user"], team_url(team), size=size, dark=True)

    assert page_overflow(page) == 0
    page.locator("#schedule").screenshot(
        path=str(SCREENS / "team-schedule" / f"team-box-{size}-dark.png")
    )


def test_the_full_season_opens_and_closes(scheduled, open_page):
    team = scheduled["teams"][0]
    total = Game.objects.for_team(team).count()
    page = open_page(scheduled["user"], team_url(team), size="phone")
    box = page.locator("#schedule")

    box.get_by_role("button", name=f"Show full season ({total} games)").click()
    expect(box.locator("li:visible")).to_have_count(total)
    assert page_overflow(page) == 0
    box.screenshot(path=str(SCREENS / "team-schedule" / "team-box-phone-full.png"))

    box.get_by_role("button", name="Show fewer").click()
    expect(box.locator("li:visible")).to_have_count(10)


@pytest.mark.parametrize("size", ["phone", "tablet"])
def test_both_pages_in_german(scheduled, open_page, size):
    user = scheduled["user"]
    user.language = "de"
    user.save(update_fields=["language"])
    team = scheduled["teams"][0]

    teams = open_page(user, reverse("nba:team-list"), size=size)
    assert teams.get_by_role("link", name="Offen").is_visible()
    assert page_overflow(teams) == 0
    shot(teams, "team-schedule", f"teams-{size}-de")

    page = open_page(user, team_url(team), size=size)
    box = page.locator("#schedule")
    assert box.get_by_role("heading", name="Spielplan").is_visible()
    assert page_overflow(page) == 0
    box.screenshot(path=str(SCREENS / "team-schedule" / f"team-box-{size}-de.png"))
