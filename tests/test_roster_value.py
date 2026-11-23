"""A roster's team value at past moments: `apps.fantasy.value`."""

import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.fantasy.models import PlayerSnapshot, Roster, RosterPlayer, Season, Transaction
from apps.fantasy.points import PointsHistory, roster_spells, spell_players
from apps.fantasy.value import roster_value_by_moment
from apps.nba.models import Player

BERLIN = ZoneInfo("Europe/Berlin")
M = Decimal(1_000_000)


def at(month, day, hour=12):
    return dt.datetime(2026, month, day, hour, tzinfo=BERLIN)


SIGNINGS_CLOSE = at(10, 20, 23)
DAY1, DAY2, DAY3 = at(10, 22, 20), at(10, 26, 20), at(10, 28, 20)
# Signed in pre-season, before signings closed.
SIGNED = at(10, 10)


@pytest.fixture
def season(db):
    return Season.objects.create(
        label="2026-27",
        starts_on=dt.date(2026, 10, 21),
        ends_on=dt.date(2027, 4, 11),
        is_current=True,
        signings_close_at=SIGNINGS_CLOSE,
        starting_cash=Decimal(60) * M,
    )


_names = iter(f"Player{i:02d}" for i in range(1000))


def player(current_salary=None):
    return Player.objects.create(
        first_name="Ann", last_name=next(_names), position="G", current_salary=current_salary
    )


def priced(season, who, salaries):
    """Snapshots of `who` at these moments, at these salaries (in millions)."""
    for moment, millions in salaries.items():
        PlayerSnapshot.objects.create(
            player=who,
            season=season,
            as_of=moment,
            salary=Decimal(millions) * M,
            total_fp=0,
            games_played=1,
            position="G",
        )


def roster(season, manager, cash):
    created = Roster.objects.create(manager=manager, season=season, name=next(_names))
    created.cash = Decimal(cash) * M
    created.save(update_fields=["cash"])
    return created


def sign(roster, who, added_at=SIGNED, removed_at=None):
    RosterPlayer.objects.create(roster=roster, player=who, added_at=added_at, removed_at=removed_at)


def millions(series):
    return [value / M for value in series]


def test_the_squad_at_each_days_salaries_plus_cash(season, manager):
    team = roster(season, manager, cash=50)
    who = player()
    sign(team, who)
    # Repriced from $3M to $4M by the update between day 1 and day 2.
    priced(season, who, {at(10, 15): 3, DAY2 - dt.timedelta(hours=1): 4})

    values = roster_value_by_moment([team], season, [SIGNINGS_CLOSE, DAY1, DAY2])

    assert millions(values[team.pk]) == [53, 53, 54]


def test_a_trade_counts_each_side_only_while_held(season, manager):
    """Sold at $4M, replaced by a $3M player: $1M comes back as cash, and the
    value is the same before and after, as a trade at market prices should be."""
    team = roster(season, manager, cash=51)  # after the trade
    out, into = player(), player()
    trade_at = at(10, 24)
    sign(team, out, removed_at=trade_at)
    sign(team, into, added_at=trade_at)
    priced(season, out, {at(10, 15): 4})
    priced(season, into, {at(10, 15): 3})
    Transaction.objects.create(
        roster=team,
        occurred_at=trade_at,
        kind=Transaction.Kind.TRADE,
        player_in=into,
        player_out=out,
        cash_delta=Decimal(1) * M,
    )

    values = roster_value_by_moment([team], season, [DAY1, trade_at, DAY2])

    # Before: $4M player + $50M cash. At and after the trade: $3M + $51M.
    assert millions(values[team.pk]) == [54, 54, 54]


def test_cash_is_anchored_on_today_so_the_newest_figure_is_the_roster_screens(season, manager):
    """A ledger that does not add up to the balance -- cash edited by hand --
    still ends on exactly the roster screen's team value."""
    team = roster(season, manager, cash=7)
    who = player(current_salary=Decimal(5) * M)
    sign(team, who)
    priced(season, who, {at(10, 15): 5})
    Transaction.objects.create(
        roster=team,
        occurred_at=at(10, 10),
        kind=Transaction.Kind.BUY,
        player_in=who,
        cash_delta=-Decimal(5) * M,
    )
    Transaction.objects.create(
        roster=team,
        occurred_at=at(10, 25),
        kind=Transaction.Kind.BUY,
        player_in=who,
        cash_delta=-Decimal(2) * M,
    )

    values = roster_value_by_moment([team], season, [DAY1, DAY2])

    team.refresh_from_db()
    assert values[team.pk][-1] == team.team_value
    # Before the second move the $2M were still in the bank.
    assert millions(values[team.pk]) == [14, 12]


def test_a_player_is_always_priced(season, manager):
    """Before his first snapshot his first one's salary stands in; without any
    snapshot his current salary does."""
    team = roster(season, manager, cash=0)
    late, never = player(), player(current_salary=Decimal(2) * M)
    sign(team, late)
    sign(team, never)
    priced(season, late, {DAY2: 6})

    values = roster_value_by_moment([team], season, [DAY1, DAY2])

    assert millions(values[team.pk]) == [8, 8]


def test_a_player_counts_from_the_moment_he_arrives(season, manager):
    team = roster(season, manager, cash=10)
    who = player()
    sign(team, who, added_at=DAY2)
    priced(season, who, {at(10, 15): 3})
    Transaction.objects.create(
        roster=team,
        occurred_at=DAY2,
        kind=Transaction.Kind.BUY,
        player_in=who,
        cash_delta=-Decimal(3) * M,
    )

    values = roster_value_by_moment([team], season, [DAY1, DAY2, DAY3])

    # The buy at DAY2 itself has happened: the player and the spent cash both count.
    assert millions(values[team.pk]) == [13, 13, 13]


def test_without_moments_or_rosters_there_is_nothing_to_add(season, manager):
    team = roster(season, manager, cash=10)

    assert roster_value_by_moment([team], season, []) == {team.pk: []}
    assert roster_value_by_moment([], season, [DAY1]) == {}


def test_a_fixed_number_of_queries_and_one_when_the_rest_is_shared(season, manager, other_manager):
    teams = [roster(season, owner, cash=5) for owner in (manager, other_manager, manager)]
    for team in teams:
        for _ in range(3):
            who = player()
            sign(team, who)
            priced(season, who, {at(10, 15): 3, DAY2: 4})
        Transaction.objects.create(
            roster=team,
            occurred_at=at(10, 25),
            kind=Transaction.Kind.BUY,
            player_in=who,
            cash_delta=0,
        )
    moments = [SIGNINGS_CLOSE, DAY1, DAY2, DAY3]

    with CaptureQueriesContext(connection) as alone:
        roster_value_by_moment(teams, season, moments)
    # Spells, snapshots, moves.
    assert len(alone) == 3

    spells = roster_spells(teams, season, DAY3)
    history = PointsHistory(season, spell_players(spells), until=DAY3)
    with CaptureQueriesContext(connection) as shared:
        roster_value_by_moment(teams, season, moments, spells=spells, history=history)
    assert len(shared) == 1
