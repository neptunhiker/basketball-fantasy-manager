"""Games and projected points between two moments, for trade plans."""

import datetime as dt
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.fantasy.models import Season
from apps.nba.models import Game, Player, PlayerInjury, Team
from apps.nba.rest_of_season import EXPECTED_AVAILABILITY, GameCounter


def berlin(*args):
    return timezone.make_aware(dt.datetime(*args))


START = berlin(2026, 11, 20, 22, 0)
# A week and a bit: next Friday night's game (01:00 Saturday) is inside.
END = berlin(2026, 11, 28, 12, 0)


@pytest.fixture
def teams(db):
    call_command("seed_teams", verbosity=0)
    return list(Team.objects.order_by("abbreviation")[:3])


@pytest.fixture
def season(db):
    return Season.objects.create(
        label="2026-27",
        starts_on=dt.date(2026, 10, 21),
        ends_on=dt.date(2027, 4, 11),
        is_current=True,
    )


_numbers = iter(range(1, 100000))


def game(home, away, when, status="STATUS_SCHEDULED"):
    return Game.objects.create(
        espn_id=f"gc{next(_numbers)}",
        season_year=2027,
        tip_off=when,
        home_team=home,
        away_team=away,
        status=status,
    )


@pytest.fixture
def schedule(teams, season):
    home, away, _ = teams
    # Two before START (one of them tonight, already under way at 21:00),
    # then three after it: tonight at 01:30, Sunday night and next Friday.
    game(home, away, berlin(2026, 11, 18, 1, 30))
    game(home, away, berlin(2026, 11, 20, 21, 0))
    game(home, away, berlin(2026, 11, 21, 1, 30))
    game(away, home, berlin(2026, 11, 23, 2, 0))
    game(home, away, berlin(2026, 11, 28, 1, 0))
    # Postponed: never counts.
    game(home, away, berlin(2026, 11, 25, 1, 0), status="STATUS_POSTPONED")
    return teams


def player(team, fp="20"):
    return Player.objects.create(
        first_name="A", last_name="B", position="G", team=team, current_fp_per_game=Decimal(fp)
    )


def test_a_window_counts_the_games_that_tip_off_inside_it(schedule, season):
    counter = GameCounter({schedule[0].pk}, season)
    guard = player(schedule[0])
    assert counter.games(guard, START, END) == 3
    assert counter.games(guard, START, berlin(2026, 11, 22, 0, 0)) == 1


def test_to_the_end_of_the_season_is_82_less_those_gone(schedule, season):
    counter = GameCounter({schedule[0].pk}, season)
    assert counter.games(player(schedule[0]), START) == 82 - 2


def test_an_injured_player_misses_the_games_before_his_return(schedule, season):
    guard = player(schedule[0])
    PlayerInjury.objects.create(
        player=guard, observed_at=START, status="Out", return_date=dt.date(2026, 11, 24)
    )
    counter = GameCounter({schedule[0].pk}, season)
    assert counter.games(guard, START, END) == 1
    assert counter.games(guard, START) == 82 - 2 - 2


def test_day_to_day_costs_nothing(schedule, season):
    guard = player(schedule[0])
    PlayerInjury.objects.create(
        player=guard, observed_at=START, status="Day-To-Day", return_date=dt.date(2026, 11, 24)
    )
    counter = GameCounter({schedule[0].pk}, season)
    assert counter.games(guard, START, END) == 3


def test_no_team_or_no_schedule_is_unknown_not_zero(schedule, season):
    counter = GameCounter({schedule[0].pk}, season)
    assert counter.games(player(None), START) is None
    assert counter.games(player(schedule[2]), START) is None
    assert not GameCounter(set(), season).has_schedule


def test_projected_points_apply_the_leagues_availability(schedule, season):
    counter = GameCounter({schedule[0].pk}, season)
    guard = player(schedule[0], fp="30")
    expected = Decimal(30) * 3 * EXPECTED_AVAILABILITY
    assert counter.projected_fp(guard, START, END) == expected
    rookie = Player.objects.create(last_name="R", position="G", team=schedule[0])
    assert counter.projected_fp(rookie, START) is None
