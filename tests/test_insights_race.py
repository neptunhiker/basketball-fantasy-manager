"""The points race behind Analytics -> Insights: `apps.analytics.insights`."""

import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.analytics.insights import points_race, race_moments
from apps.analytics.models import DreamTeam
from apps.fantasy.models import Manager, PlayerSnapshot, Roster, RosterPlayer, Season
from apps.nba.models import Player

BERLIN = ZoneInfo("Europe/Berlin")


def at(month, day, hour=12):
    return dt.datetime(2026, month, day, hour, tzinfo=BERLIN)


SIGNINGS_CLOSE = at(10, 20, 23)


@pytest.fixture
def season(db):
    return Season.objects.create(
        label="2026-27",
        starts_on=dt.date(2026, 10, 21),
        ends_on=dt.date(2027, 4, 11),
        is_current=True,
        signings_close_at=SIGNINGS_CLOSE,
        starting_cash=Decimal("60000000"),
    )


_names = iter(f"Player{i:02d}" for i in range(1000))


def player():
    return Player.objects.create(first_name="Ann", last_name=next(_names), position="G")


def snapshot(season, who, moment, total):
    PlayerSnapshot.objects.create(
        player=who,
        season=season,
        as_of=moment,
        salary=Decimal("3000000"),
        total_fp=Decimal(total),
        games_played=1,
        position="G",
    )


def dream_team(season, moment, total):
    return DreamTeam.objects.create(
        season=season,
        as_of=moment,
        draft_as_of=at(10, 1),
        budget=Decimal("60000000"),
        total_salary=Decimal("59000000"),
        total_fp=Decimal(total),
        candidates=300,
    )


def roster_with(season, manager, name, totals_by_moment, **extra):
    """A roster of one player, signed before the cutoff, scoring these totals."""
    roster = Roster.objects.create(manager=manager, season=season, name=name, **extra)
    who = player()
    RosterPlayer.objects.create(roster=roster, player=who, added_at=at(10, 10))
    for moment, total in totals_by_moment.items():
        snapshot(season, who, moment, total)
    return roster


def values(line):
    return [None if value is None else int(value) for value in line.values]


def test_nothing_to_draw_before_the_first_import_after_signings_close(season, manager):
    roster_with(season, manager, "Koalas", {at(10, 15): 0})

    assert points_race(season) is None


def test_one_figure_per_day_the_last_import_of_each(season):
    who = player()
    for moment in (at(10, 21, 8), at(10, 21, 20), at(10, 23, 12)):
        snapshot(season, who, moment, 1)

    start, moments = race_moments(season)

    assert start == SIGNINGS_CLOSE
    assert moments == [at(10, 21, 20), at(10, 23, 12)]


def test_every_line_starts_at_zero_on_the_day_signings_closed(season, manager, user):
    day1, day2 = at(10, 21, 20), at(10, 22, 20)
    roster_with(season, manager, "Koalas", {day1: 30, day2: 55})
    dream_team(season, day1, 400)
    dream_team(season, day2, 820)

    race = points_race(season, viewer=user)

    assert race.days == [dt.date(2026, 10, 20), dt.date(2026, 10, 21), dt.date(2026, 10, 22)]
    assert race.as_of == day2
    assert values(race.dream_team) == [0, 400, 820]
    [koalas] = race.rosters
    assert values(koalas) == [0, 30, 55]
    assert koalas.name == "Koalas"
    assert koalas.manager == "Bulla"
    assert koalas.is_own is True
    assert koalas.last_gain == 25


def test_an_import_on_the_cutoff_day_takes_the_starts_place(season):
    season.signings_close_at = at(10, 20, 8)
    season.save()
    snapshot(season, player(), at(10, 20, 22), 0)

    race = points_race(season)

    assert race.days == [dt.date(2026, 10, 20)]


def test_inactive_rosters_are_left_out(season, manager):
    day1 = at(10, 21, 20)
    roster_with(season, manager, "Real", {day1: 10})
    roster_with(season, manager, "What if", {day1: 99}, is_active=False)

    race = points_race(season)

    assert [line.name for line in race.rosters] == ["Real"]


def test_other_seasons_rosters_are_left_out(season, manager):
    old = Season.objects.create(
        label="2025-26", starts_on=dt.date(2025, 10, 21), ends_on=dt.date(2026, 4, 11)
    )
    day1 = at(10, 21, 20)
    roster_with(season, manager, "Now", {day1: 10})
    Roster.objects.create(manager=manager, season=old, name="Then")

    assert [line.name for line in points_race(season).rosters] == ["Now"]


def test_rosters_are_ordered_by_points_and_other_managers_are_not_own(
    season, manager, other_manager, user
):
    day1 = at(10, 21, 20)
    roster_with(season, manager, "Mine", {day1: 10})
    roster_with(season, other_manager, "Theirs", {day1: 50})

    race = points_race(season, viewer=user)

    assert [(line.name, line.is_own) for line in race.rosters] == [
        ("Theirs", False),
        ("Mine", True),
    ]


def test_a_day_without_a_dream_team_is_a_gap_not_a_zero(season, manager):
    day1, day2, day3 = at(10, 21, 20), at(10, 22, 20), at(10, 23, 20)
    roster_with(season, manager, "Koalas", {day1: 1, day2: 2, day3: 3})
    dream_team(season, day1, 100)
    dream_team(season, day3, 300)

    race = points_race(season)

    assert values(race.dream_team) == [0, 100, None, 300]
    assert race.dream_team.latest == 300


def test_standings_rank_every_line_with_its_gap_to_the_dream_team(season, manager, other_manager):
    day1 = at(10, 21, 20)
    dream_team(season, day1, 100)
    roster_with(season, manager, "Close", {day1: 90})
    roster_with(season, other_manager, "Ahead", {day1: 120})

    rows = [(rank, line.name, gap) for rank, line, gap in points_race(season).standings]

    assert rows == [(1, "Ahead", -20), (2, "Dream Team", None), (3, "Close", 10)]


def test_without_rosters_the_dream_team_runs_alone(season):
    day1 = at(10, 21, 20)
    snapshot(season, player(), day1, 5)
    dream_team(season, day1, 100)

    race = points_race(season)

    assert race.rosters == []
    assert values(race.dream_team) == [0, 100]


def test_the_race_takes_a_fixed_number_of_queries(season, manager, other_manager):
    days = [at(10, 21 + offset, 20) for offset in range(5)]
    for owner in (manager, other_manager, manager):
        roster_with(
            season,
            owner,
            f"Roster {next(_names)}",
            {day: 10 * index for index, day in enumerate(days)},
        )
    for day in days:
        dream_team(season, day, 500)
    Manager.objects.create(user=manager.user, nick_name="Second profile")

    with CaptureQueriesContext(connection) as queries:
        race = points_race(season)

    # Import moments, rosters with managers, spells, snapshots, Dream Teams.
    assert len(queries) == 5
    assert len(race.rosters) == 3
