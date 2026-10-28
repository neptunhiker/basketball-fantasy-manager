"""A player's rest of the season: games expected, the next days, projected points."""

import datetime as dt
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.fantasy.models import Season
from apps.nba.models import Game, Player, PlayerInjury, Team
from apps.nba.rest_of_season import EXPECTED_AVAILABILITY, rest_of_season, with_rest_of_season
from apps.nba.views import with_expected_return


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


def game(home, away, days, season):
    """A game `days` from now, at 02:00 Berlin time -- an evening in America."""
    when = timezone.localtime() + dt.timedelta(days=days)
    return Game.objects.create(
        espn_id=str(next(_numbers)),
        season_year=season.ends_on.year,
        tip_off=when.replace(hour=2, minute=0, second=0, microsecond=0),
        home_team=home,
        away_team=away,
        status="STATUS_SCHEDULED",
    )


def player(team, fp_per_game="20.00", name="Smart"):
    return Player.objects.create(
        first_name="Marcus",
        last_name=name,
        position="G",
        team=team,
        current_fp_per_game=Decimal(fp_per_game) if fp_per_game else None,
    )


def hurt(someone, status="Out", back_in_days=None):
    PlayerInjury.objects.create(
        player=someone,
        observed_at=timezone.now(),
        status=status,
        return_date=timezone.localdate() + dt.timedelta(days=back_in_days)
        if back_in_days is not None
        else None,
    )


def annotated(someone):
    queryset = with_expected_return(Player.objects.filter(pk=someone.pk))
    return with_rest_of_season(queryset).get()


@pytest.fixture
def schedule(teams, season):
    """Houston: 2 played, then a game every two days for twenty days."""
    hou, lal = teams["HOU"], teams["LAL"]
    game(hou, lal, -4, season)
    game(lal, hou, -2, season)
    for day in range(1, 21, 2):
        game(hou, lal, day, season)
    return teams


def test_a_healthy_player_expects_all_his_team_s_games(schedule):
    smart = player(schedule["HOU"])

    row = annotated(smart)

    assert (row.team_games_left, row.games_missed, row.games_expected) == (80, 0, 80)
    # Days 1, 3, 5 and 7 (at 02:00, before this time a week on) are in the next seven days.
    assert row.next_days == 4
    assert row.projected_fp == round(Decimal("20.00") * 80 * EXPECTED_AVAILABILITY) == 1360


def test_an_injury_with_a_return_date_costs_the_games_before_it(schedule):
    smart = player(schedule["HOU"])
    # Back on day 6: misses the games on days 1, 3 and 5.
    hurt(smart, back_in_days=6)

    row = annotated(smart)

    # Of the next seven days' four games he can play only day 7's.
    assert (row.games_missed, row.games_expected, row.next_days) == (3, 77, 1)
    assert row.projected_fp == round(Decimal("20.00") * 77 * EXPECTED_AVAILABILITY)


def test_day_to_day_or_no_return_date_costs_nothing(schedule):
    day_to_day = player(schedule["HOU"], name="Daily")
    hurt(day_to_day, status="Day-To-Day", back_in_days=6)
    no_date = player(schedule["HOU"], name="Unknown")
    hurt(no_date, status="Out")

    assert annotated(day_to_day).games_missed == 0
    assert annotated(no_date).games_missed == 0


def test_a_player_back_to_health_misses_nothing(schedule):
    smart = player(schedule["HOU"])
    hurt(smart, back_in_days=6)
    hurt(smart, status="Healthy")

    assert annotated(smart).games_missed == 0


def test_out_past_the_last_game_expects_only_the_unscheduled_ones(schedule):
    smart = player(schedule["HOU"])
    hurt(smart, back_in_days=200)

    row = annotated(smart)

    # The ten stored games are lost; the 70 not on the schedule yet are not.
    assert (row.games_missed, row.games_expected) == (10, 70)


def test_without_points_or_a_team_there_is_no_projection(schedule):
    rookie = player(schedule["HOU"], fp_per_game=None, name="Rookie")
    free_agent = player(None, name="Free")

    assert annotated(rookie).projected_fp is None
    assert annotated(rookie).games_expected == 80
    assert annotated(free_agent).games_expected is None
    assert annotated(free_agent).projected_fp is None


def test_without_a_schedule_every_figure_is_empty(teams, season):
    row = annotated(player(teams["HOU"]))

    assert row.games_expected is None
    assert row.projected_fp is None


def test_players_sort_by_projected_points(schedule):
    player(schedule["HOU"], fp_per_game="30.00", name="Star")
    hurt_star = player(schedule["HOU"], fp_per_game="32.00", name="Hurt")
    hurt(hurt_star, back_in_days=200)
    player(schedule["HOU"], fp_per_game="10.00", name="Bench")

    rows = with_rest_of_season(with_expected_return(Player.objects.all())).order_by("-projected_fp")

    assert [row.last_name for row in rows] == ["Star", "Hurt", "Bench"]


def test_the_list_costs_the_same_however_many_players(schedule):
    player(schedule["HOU"], name="One")
    with CaptureQueriesContext(connection) as one:
        list(with_rest_of_season(with_expected_return(Player.objects.all())))
    for index in range(10):
        someone = player(schedule["HOU"], name=f"P{index}")
        hurt(someone, back_in_days=index)
    with CaptureQueriesContext(connection) as eleven:
        list(with_rest_of_season(with_expected_return(Player.objects.all())))

    assert len(eleven) == len(one)


# -- one player, for his page ----------------------------------------------------


def test_one_player_s_figures_match_the_list(schedule):
    smart = player(schedule["HOU"])
    hurt(smart, back_in_days=6)
    smart = Player.objects.get(pk=smart.pk)

    figures = rest_of_season(smart)
    row = annotated(smart)

    assert (figures.games_missed, figures.games_expected, figures.next_days) == (
        row.games_missed,
        row.games_expected,
        row.next_days,
    )
    assert figures.projected_fp == row.projected_fp
    assert figures.return_date == timezone.localdate() + dt.timedelta(days=6)
    assert figures.availability_percent == 85


def test_one_player_without_a_team_or_schedule_has_no_figures(teams, season):
    assert rest_of_season(player(None)) is None
    assert rest_of_season(player(teams["HOU"], name="Other")) is None
