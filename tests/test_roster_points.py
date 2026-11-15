"""A roster's points so far, moment by moment: `apps.fantasy.points`.

Rows are written directly rather than through `services`, because the dates
are the point of every test here and the services stamp them with `now`.
"""

import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.fantasy.models import PlayerSnapshot, Roster, RosterPlayer, Season
from apps.fantasy.points import PointsHistory, cutoff, roster_points_by_moment
from apps.fantasy.timeline import roster_timeline
from apps.nba.models import Player

BERLIN = ZoneInfo("Europe/Berlin")


def at(month, day, hour=12):
    year = 2026 if month >= 7 else 2027
    return dt.datetime(year, month, day, hour, tzinfo=BERLIN)


SIGNINGS_CLOSE = at(10, 20, 23)
# The daily imports: the evening of each of the season's first days.
DAY1, DAY2, DAY3, DAY4 = (at(10, day, 20) for day in (21, 22, 23, 24))


@pytest.fixture
def season(db):
    return Season.objects.create(
        label="2026-27",
        starts_on=dt.date(2026, 10, 21),
        ends_on=dt.date(2027, 4, 11),
        is_current=True,
        signings_close_at=SIGNINGS_CLOSE,
    )


@pytest.fixture
def roster(season, manager):
    return Roster.objects.create(manager=manager, season=season, name="Koalas")


_names = iter(f"Player{i:02d}" for i in range(1000))


def player():
    return Player.objects.create(first_name="Ann", last_name=next(_names), position="G")


def scores(season, who, *totals, moments=(DAY1, DAY2, DAY3, DAY4)):
    """His season total at each import, from the first one on."""
    for moment, total in zip(moments, totals, strict=False):
        PlayerSnapshot.objects.create(
            player=who,
            season=season,
            as_of=moment,
            salary=Decimal("3000000"),
            total_fp=Decimal(total),
            games_played=1,
            position="G",
        )


def spell(roster, who, added, removed=None):
    return RosterPlayer.objects.create(
        roster=roster, player=who, added_at=added, removed_at=removed
    )


def series(roster, season, moments=(DAY1, DAY2, DAY3, DAY4)):
    return [int(value) for value in roster_points_by_moment([roster], season, moments)[roster.pk]]


def test_the_cutoff_is_when_signings_closed(season):
    assert cutoff(season) == SIGNINGS_CLOSE


def test_without_a_signings_cutoff_the_season_start_stands_in(season):
    season.signings_close_at = None

    assert cutoff(season) == season.season_open_at


def test_a_squad_that_never_changes_adds_up_its_players(season, roster):
    first, second = player(), player()
    spell(roster, first, at(10, 10))
    spell(roster, second, at(10, 10))
    scores(season, first, 10, 25, 40, 50)
    scores(season, second, 5, 5, 20, 30)

    assert series(roster, season) == [15, 30, 60, 80]


def test_a_traded_player_keeps_what_he_scored_and_his_successor_adds_on(season, roster):
    out, incoming = player(), player()
    spell(roster, out, at(10, 10), removed=at(10, 23, 9))
    spell(roster, incoming, at(10, 23, 9))
    scores(season, out, 10, 20, 35, 50)
    scores(season, incoming, 100, 110, 130, 145)

    # Out scored 20 before the trade; incoming adds 130-110 and then 145-110.
    assert series(roster, season) == [10, 20, 40, 55]


def test_a_player_sold_and_bought_back_counts_only_while_he_was_there(season, roster):
    who = player()
    spell(roster, who, at(10, 10), removed=at(10, 22, 21))
    spell(roster, who, at(10, 23, 21))
    scores(season, who, 10, 20, 30, 45)

    # 20 in the first spell, then 45-30 after coming back.
    assert series(roster, season) == [10, 20, 20, 35]


def test_spells_that_ended_before_the_cutoff_do_not_count(season, roster):
    tried, kept = player(), player()
    spell(roster, tried, at(10, 10), removed=at(10, 15))
    spell(roster, kept, at(10, 10))
    scores(season, tried, 50, 60, 70, 80)
    scores(season, kept, 1, 2, 3, 4)

    assert series(roster, season) == [1, 2, 3, 4]


def test_moments_before_the_cutoff_are_zero(season, roster):
    who = player()
    spell(roster, who, at(10, 10))
    scores(season, who, 10)

    assert series(roster, season, moments=(at(10, 19), SIGNINGS_CLOSE, DAY1)) == [0, 0, 10]


def test_a_mid_season_arrival_with_no_earlier_snapshot_adds_nothing(season, roster):
    """His first total would include games played for nobody, so he is left
    out rather than counted wrongly -- the same rule as the roster timeline."""
    newcomer = player()
    spell(roster, newcomer, at(10, 22, 9))
    scores(season, newcomer, 40, 55, moments=(DAY3, DAY4))

    assert series(roster, season) == [0, 0, 0, 0]


def test_a_correction_never_takes_points_away(season, roster):
    """Bought on day two at a total of 30 that a later import corrected to 25:
    he scored nothing for the roster, he did not cost it five."""
    who = player()
    spell(roster, who, at(10, 22, 9))
    scores(season, who, 30, 25, moments=(DAY1, DAY2))

    assert series(roster, season, moments=(DAY1, DAY2)) == [0, 0]


def test_no_moments_or_no_rosters(season, roster):
    assert roster_points_by_moment([roster], season, []) == {roster.pk: []}
    assert roster_points_by_moment([], season, [DAY1]) == {}


def test_several_rosters_take_two_queries(season, roster, manager):
    other = Roster.objects.create(manager=manager, season=season, name="Other")
    for target in (roster, other):
        for _ in range(15):
            who = player()
            spell(target, who, at(10, 10))
            scores(season, who, 10, 20, 30, 40)

    with CaptureQueriesContext(connection) as queries:
        result = roster_points_by_moment([roster, other], season, [DAY1, DAY2, DAY3, DAY4])

    assert len(queries) == 2
    assert [int(value) for value in result[other.pk]] == [150, 300, 450, 600]


def test_the_last_figure_matches_the_roster_timeline(season, roster):
    """The chart and the roster page must never disagree about a roster."""
    out, incoming, stays = player(), player(), player()
    spell(roster, out, at(10, 10), removed=at(10, 23, 9))
    spell(roster, incoming, at(10, 23, 9))
    spell(roster, stays, at(10, 10))
    scores(season, out, 10, 20, 35, 50)
    scores(season, incoming, 100, 110, 130, 145)
    scores(season, stays, 7, 14, 21, 28)

    timeline = roster_timeline(roster, now=DAY4)
    by_timeline = sum(
        int(stint["points"].replace(",", ""))
        for spot in timeline.spots
        for stint in spot["stints"]
        if stint["points"] is not None
    )

    assert series(roster, season)[-1] == by_timeline == 83


def test_points_history_reads_the_last_snapshot_at_or_before_a_moment(season):
    who = player()
    scores(season, who, 10, 20)
    history = PointsHistory(season, [who.pk])

    assert history.total_at(who.pk, at(10, 21, 8)) is None
    assert history.total_at(who.pk, DAY1) == 10
    assert history.total_at(who.pk, at(10, 22, 9)) == 10
    assert history.total_at(who.pk, DAY4) == 20
