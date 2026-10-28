"""Games played, left and coming up per team -- worked out and on the team list."""

import datetime as dt

import pytest
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.fantasy.models import Season
from apps.nba.models import Game, Team
from apps.nba.team_schedule import team_schedules

TEAM_LIST = reverse("nba:team-list")


@pytest.fixture
def signed_in(client, user, password):
    client.login(email=user.email, password=password)
    return client


@pytest.fixture
def teams(db):
    call_command("seed_teams", verbosity=0)
    return {team.abbreviation: team for team in Team.objects.all()}


@pytest.fixture
def season(db):
    today = timezone.localdate()
    return Season.objects.create(
        label="now",
        starts_on=today - dt.timedelta(days=30),
        ends_on=today + dt.timedelta(days=150),
        is_current=True,
    )


_numbers = iter(range(1, 100000))


def game(home, away, when, season, **fields):
    return Game.objects.create(
        espn_id=str(next(_numbers)),
        season_year=season.ends_on.year,
        tip_off=when,
        home_team=home,
        away_team=away,
        status=fields.pop("status", "STATUS_SCHEDULED"),
        **fields,
    )


def hours(n):
    return timezone.now() + dt.timedelta(hours=n)


# -- the figures -----------------------------------------------------------------


def test_played_left_and_the_next_days_per_team(teams, season):
    bos, lal = teams["BOS"], teams["LAL"]
    game(bos, lal, hours(-48), season)
    game(lal, bos, hours(-24), season)
    later = game(bos, lal, hours(30), season)
    game(lal, bos, hours(24 * 9), season)

    boston = team_schedules()[bos.pk]

    assert (boston.played, boston.left, boston.next_days) == (2, 80, 1)
    assert boston.next_game == later
    assert boston.next_opponent == lal
    assert boston.next_game_is_home
    assert boston.progress == 2


def test_a_game_under_way_is_neither_played_nor_past(teams, season):
    bos, lal = teams["BOS"], teams["LAL"]
    on_now = game(bos, lal, hours(-1), season)

    boston = team_schedules()[bos.pk]

    assert boston.played == 0
    assert boston.next_game == on_now
    assert boston.next_game_is_live


def test_postponed_preseason_and_other_seasons_do_not_count(teams, season):
    bos, lal = teams["BOS"], teams["LAL"]
    game(bos, lal, hours(-48), season)
    game(bos, lal, hours(-40), season, status="STATUS_POSTPONED")
    game(bos, lal, hours(-30), season, is_regular_season=False)
    Game.objects.create(
        espn_id="old",
        season_year=season.ends_on.year - 1,
        tip_off=hours(-24 * 200),
        home_team=bos,
        away_team=lal,
    )

    assert team_schedules()[bos.pk].played == 1


def test_without_a_schedule_or_a_season_there_are_no_figures(teams, season):
    assert team_schedules() == {}

    game(teams["BOS"], teams["LAL"], hours(-48), season)
    season.delete()
    assert team_schedules() == {}


def test_left_never_goes_below_zero(teams, season):
    bos, lal = teams["BOS"], teams["LAL"]
    for day in range(83):
        game(bos, lal, hours(-24 * (day + 1)), season)

    boston = team_schedules()[bos.pk]

    assert (boston.played, boston.left, boston.progress) == (83, 0, 100)


# -- the team list ---------------------------------------------------------------


def test_the_team_list_shows_left_the_next_days_and_the_next_game(signed_in, teams, season):
    bos, lal = teams["BOS"], teams["LAL"]
    game(bos, lal, hours(-48), season)
    game(lal, bos, hours(2), season)

    response = signed_in.get(TEAM_LIST)

    boston = next(team for team in response.context["teams"] if team.abbreviation == "BOS")
    assert (boston.schedule.left, boston.schedule.next_days) == (81, 1)
    page = response.content.decode()
    assert "1 played" in page
    assert f'href="{reverse("nba:team-detail", args=["lal"])}"' in page
    assert "@" in page and "vs" in page


def test_without_a_schedule_the_columns_stay_away(signed_in, teams, season):
    response = signed_in.get(TEAM_LIST)

    assert not response.context["has_schedule"]
    assert all(team.schedule is None for team in response.context["teams"])
    page = response.content.decode()
    assert 'role="progressbar"' not in page
    assert "Next game" not in page


def test_the_team_list_ranks_by_games_left(signed_in, teams, season):
    bos, lal, mia = teams["BOS"], teams["LAL"], teams["MIA"]
    game(bos, lal, hours(-48), season)
    game(bos, mia, hours(-24), season)
    game(mia, lal, hours(24), season)

    response = signed_in.get(TEAM_LIST, {"sort": "left"})

    ranked = [team.abbreviation for team in response.context["teams"]]
    # LAL and MIA have played once, BOS twice.
    assert set(ranked[:2]) == {"LAL", "MIA"}
    assert ranked[2] == "BOS"
    # Teams without a game this season come after every team with one.
    assert set(ranked[3:]) == set(teams) - {"BOS", "LAL", "MIA"}


def test_the_team_list_ranks_by_games_in_the_next_days(signed_in, teams, season):
    bos, lal, mia = teams["BOS"], teams["LAL"], teams["MIA"]
    game(bos, lal, hours(10), season)
    game(lal, mia, hours(40), season)
    game(lal, bos, hours(24 * 10), season)

    response = signed_in.get(TEAM_LIST, {"sort": "next"})

    ranked = [team.abbreviation for team in response.context["teams"]]
    assert ranked[0] == "LAL"
    assert response.context["ranked"]


def test_the_next_game_today_says_today(signed_in, teams, season):
    tonight = timezone.localtime().replace(hour=23, minute=30, second=0, microsecond=0)
    if tonight <= timezone.localtime():
        pytest.skip("too late in the day to schedule a game still to come today")
    game(teams["BOS"], teams["LAL"], tonight, season)

    page = signed_in.get(TEAM_LIST).content.decode()

    assert "Today" in page
    assert "23:30" in page


def test_the_team_list_costs_the_same_however_many_games(signed_in, teams, season):
    bos, lal = teams["BOS"], teams["LAL"]
    game(bos, lal, hours(-24), season)
    with CaptureQueriesContext(connection) as few:
        signed_in.get(TEAM_LIST)
    for day in range(40):
        game(lal, bos, hours(24 * (day - 20) + 5), season)
    with CaptureQueriesContext(connection) as many:
        signed_in.get(TEAM_LIST)

    assert len(many) == len(few)
