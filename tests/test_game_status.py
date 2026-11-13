"""Played, no game or DNP: the schedule laid over the snapshots."""

import datetime as dt
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.fantasy.models import PlayerSnapshot, Season
from apps.nba import game_status
from apps.nba.game_status import (
    CORRECTED,
    MISSED,
    NO_GAME,
    NO_TEAM,
    PARTLY,
    PLAYED,
    SEASON_START,
    attach_latest_status,
    snapshot_history,
)
from apps.nba.models import Game, Player, PlayerInjury, Team
from apps.nba.views import with_latest_fp_change

pytestmark = pytest.mark.django_db

# Every test reads in "days" from a fixed start, at noon Berlin-ish UTC: a game
# at 01:00 on day n is over by 04:00, long before the noon read of day n.
START = timezone.now().replace(hour=12, minute=0, second=0, microsecond=0) - dt.timedelta(days=20)


def day(n, hour=12):
    return START + dt.timedelta(days=n, hours=hour - 12)


@pytest.fixture
def teams(db):
    call_command("seed_teams", verbosity=0)
    return {team.abbreviation: team for team in Team.objects.all()}


@pytest.fixture
def season(db):
    today = timezone.localdate()
    return Season.objects.create(
        label="2026-27",
        starts_on=today - dt.timedelta(days=60),
        ends_on=today + dt.timedelta(days=150),
        is_current=True,
    )


_numbers = iter(range(1, 100000))


def game(home, away, when, season, **fields):
    return Game.objects.create(
        espn_id=f"gs{next(_numbers)}",
        season_year=season.ends_on.year,
        tip_off=when,
        home_team=home,
        away_team=away,
        status=fields.pop("status", "STATUS_FINAL"),
        **fields,
    )


def player(team, name="Flagg"):
    return Player.objects.create(first_name="C", last_name=name, position="F", team=team)


def snap(who, season, when, games, points=None, team="same"):
    return PlayerSnapshot.objects.create(
        player=who,
        season=season,
        as_of=when,
        salary=Decimal("5000000"),
        total_fp=Decimal(points if points is not None else games * 30),
        games_played=games,
        team=who.team if team == "same" else team,
        position="F",
    )


def injury(who, when, status="Out", location="Ankle"):
    return PlayerInjury.objects.create(
        player=who, observed_at=when, status=status, injury_location=location
    )


def history(who):
    snapshots = list(who.snapshots.order_by("-as_of"))
    return snapshot_history(who, snapshots)


# --- one window ------------------------------------------------------------------


def test_a_game_he_played_names_the_opponent_and_where(teams, season):
    dal, bos = teams["DAL"], teams["BOS"]
    flagg = player(dal)
    snap(flagg, season, day(0), 4)
    game(bos, dal, day(1, hour=1), season)
    snap(flagg, season, day(1), 5)

    statuses, _ = history(flagg)

    newest = statuses[0]
    assert newest.kind == PLAYED and newest.played == 1
    [opponent] = newest.opponents
    assert opponent.team == bos and not opponent.is_home
    assert statuses[1].kind == SEASON_START


def test_his_team_played_without_him_with_the_injury_at_tip_off(teams, season):
    dal, lal = teams["DAL"], teams["LAL"]
    flagg = player(dal)
    injury(flagg, day(0, hour=8), status="Out")
    snap(flagg, season, day(0), 4)
    game(dal, lal, day(1, hour=1), season)
    snap(flagg, season, day(1), 4)

    status = history(flagg)[0][0]

    assert status.kind == MISSED and status.missed == 1
    assert status.is_injured and status.injury.injury_location == "Ankle"
    assert status.opponents[0].is_home


def test_a_healthy_report_before_tip_off_is_no_reason(teams, season):
    dal, lal = teams["DAL"], teams["LAL"]
    flagg = player(dal)
    injury(flagg, day(-3), status="Out")
    injury(flagg, day(0, hour=8), status="Healthy")
    # Reported after the game: not what was known at tip-off.
    injury(flagg, day(1, hour=9), status="Day-To-Day")
    snap(flagg, season, day(0), 4)
    game(dal, lal, day(1, hour=1), season)
    snap(flagg, season, day(1), 4)

    status = history(flagg)[0][0]

    assert status.kind == MISSED
    assert status.injury is None


def test_some_of_two_games(teams, season):
    dal, lal, phx = teams["DAL"], teams["LAL"], teams["PHX"]
    flagg = player(dal)
    injury(flagg, day(1, hour=18), status="Day-To-Day")
    snap(flagg, season, day(0), 4)
    game(dal, lal, day(1, hour=1), season)
    game(phx, dal, day(2, hour=3), season)
    snap(flagg, season, day(2), 5)

    status = history(flagg)[0][0]

    assert status.kind == PARTLY
    assert (status.played, status.missed, status.game_count) == (1, 1, 2)
    assert status.is_day_to_day


def test_no_game_and_a_rest_day(teams, season):
    dal, lal = teams["DAL"], teams["LAL"]
    flagg = player(dal)
    # Another team's game is none of his business.
    game(lal, teams["BOS"], day(1, hour=1), season)
    snap(flagg, season, day(0), 4)
    snap(flagg, season, day(1), 4)

    status = history(flagg)[0][0]

    assert status.kind == NO_GAME and status.is_quiet


def test_a_game_still_on_belongs_to_the_next_read(teams, season):
    dal, lal = teams["DAL"], teams["LAL"]
    flagg = player(dal)
    snap(flagg, season, day(0), 4)
    game(dal, lal, day(1, hour=10), season)  # over at 13:00, read at 12:00
    snap(flagg, season, day(1), 4)
    snap(flagg, season, day(2), 5)

    statuses, _ = history(flagg)

    assert [status.kind for status in statuses] == [PLAYED, NO_GAME, SEASON_START]


def test_a_game_counted_late_is_played_never_dnp(teams, season):
    dal, lal, bos = teams["DAL"], teams["LAL"], teams["BOS"]
    flagg = player(dal)
    snap(flagg, season, day(0), 4)
    early = game(dal, lal, day(-1, hour=1), season)
    game(bos, dal, day(1, hour=1), season)
    snap(flagg, season, day(1), 6)

    status = history(flagg)[0][0]

    assert status.kind == PLAYED and status.played == 2
    assert [opponent.team for opponent in status.opponents] == [lal, bos]
    assert status.games[0] == early


def test_more_games_than_the_schedule_has(teams, season):
    flagg = player(teams["DAL"])
    snap(flagg, season, day(0), 0)
    snap(flagg, season, day(1), 2)

    status = history(flagg)[0][0]

    assert status.kind == PLAYED and status.played == 2 and status.opponents == []


def test_traded_between_two_reads_counts_both_teams(teams, season):
    dal, lal, bos = teams["DAL"], teams["LAL"], teams["BOS"]
    flagg = player(dal)
    snap(flagg, season, day(0), 4)
    game(dal, bos, day(1, hour=1), season)
    game(lal, bos, day(2, hour=1), season)
    snap(flagg, season, day(2), 5, team=lal)

    status = history(flagg)[0][0]

    assert status.kind == PARTLY and status.game_count == 2
    assert [opponent.team for opponent in status.opponents] == [bos, bos]


def test_postponed_and_preseason_games_do_not_count(teams, season):
    dal, lal = teams["DAL"], teams["LAL"]
    flagg = player(dal)
    snap(flagg, season, day(0), 4)
    game(dal, lal, day(1, hour=1), season, status="STATUS_POSTPONED")
    game(dal, lal, day(1, hour=2), season, is_regular_season=False)
    snap(flagg, season, day(1), 4)

    assert history(flagg)[0][0].kind == NO_GAME


def test_edge_states(teams, season):
    flagg = player(teams["DAL"])
    snap(flagg, season, day(0), 5)
    snap(flagg, season, day(1), 4)
    free = player(None, name="Free")
    snap(free, season, day(0), 4)
    snap(free, season, day(1), 4)

    assert history(flagg)[0][0].kind == CORRECTED
    assert history(free)[0][0].kind == NO_TEAM


def test_a_new_season_starts_over(teams, season):
    dal, lal = teams["DAL"], teams["LAL"]
    old = Season.objects.create(
        label="2025-26", starts_on=day(-200).date(), ends_on=day(-60).date()
    )
    flagg = player(dal)
    snap(flagg, old, day(-70), 70)
    game(dal, lal, day(1, hour=1), season)
    snap(flagg, season, day(2), 1)

    assert history(flagg)[0][0].kind == SEASON_START


# --- the summary --------------------------------------------------------------------


def test_the_season_summary_counts_team_games_against_games_played(teams, season):
    dal, lal, bos = teams["DAL"], teams["LAL"], teams["BOS"]
    flagg = player(dal)
    game(dal, lal, day(-2, hour=1), season)  # before the first read: played, it says 1
    snap(flagg, season, day(0), 1)
    injury(flagg, day(0, hour=13))
    game(dal, bos, day(1, hour=1), season)
    snap(flagg, season, day(1), 1)
    snap(flagg, season, day(2), 1)  # no game
    injury(flagg, day(2, hour=13), status="Healthy")
    game(lal, dal, day(3, hour=1), season)
    game(bos, dal, day(4, hour=1), season)
    snap(flagg, season, day(4), 2)

    _, summary = history(flagg)

    assert (summary.played, summary.team_games, summary.missed) == (2, 4, 2)
    assert summary.played_share == 50
    assert (summary.injured, summary.unreported) == (1, 1)
    assert summary.has_breakdown


def test_no_summary_before_his_team_has_played(teams, season):
    flagg = player(teams["DAL"])
    snap(flagg, season, day(0), 0)

    assert history(flagg)[1] is None


def test_the_player_page_costs_two_queries_however_long_the_history(teams, season):
    dal, lal = teams["DAL"], teams["LAL"]
    flagg = player(dal)
    for n in range(30):
        game(dal, lal, day(n, hour=1), season)
        snap(flagg, season, day(n), n)
    snapshots = list(flagg.snapshots.order_by("-as_of"))

    with CaptureQueriesContext(connection) as queries:
        statuses, _ = snapshot_history(flagg, snapshots)
        [status.opponents for status in statuses]

    assert len(queries) == 2


# --- the lists --------------------------------------------------------------------


def test_the_list_status_reads_the_annotations(teams, season):
    dal, lal = teams["DAL"], teams["LAL"]
    resting, sitting = player(dal, "Rest"), player(lal, "Sit")
    game(lal, teams["BOS"], day(1, hour=1), season)
    for who, games in ((resting, 4), (sitting, 4)):
        snap(who, season, day(0), games)
        snap(who, season, day(1), games)
    # The newest snapshot is mirrored on the player by the import.
    Player.objects.update(current_games_played=4)

    players = list(with_latest_fp_change(Player.objects.filter(pk__in=[resting.pk, sitting.pk])))
    with CaptureQueriesContext(connection) as queries:
        attach_latest_status(players)

    by_name = {who.last_name: who.latest_status for who in players}
    assert by_name["Rest"].kind == NO_GAME
    assert by_name["Sit"].kind == MISSED
    assert len(queries) == 2


def test_a_single_snapshot_has_no_list_status(teams, season):
    flagg = player(teams["DAL"])
    snap(flagg, season, day(0), 4)

    [annotated] = attach_latest_status(with_latest_fp_change(Player.objects.filter(pk=flagg.pk)))

    assert annotated.latest_status is None


def test_settled_at_follows_the_delay(monkeypatch, teams, season):
    match = game(teams["DAL"], teams["LAL"], day(0, hour=1), season)
    assert game_status.settled_at(match) == day(0, hour=4)
    monkeypatch.setattr(game_status, "SOURCE_DELAY", dt.timedelta(hours=2))
    assert game_status.settled_at(match) == day(0, hour=6)
