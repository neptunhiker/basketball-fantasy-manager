import datetime as dt
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db.utils import IntegrityError

from apps.fantasy import services
from apps.fantasy.models import STARTING_CASH, Roster, RosterPlayer, Season, Transaction
from apps.nba.models import Player


@pytest.fixture
def season(db):
    return Season.objects.create(
        label="2025-26",
        starts_on=dt.date(2025, 10, 21),
        ends_on=dt.date(2026, 4, 12),
        is_current=True,
    )


@pytest.fixture
def roster(season, manager):
    return Roster.objects.create(manager=manager, season=season, name="My roster")


def make_player(position, name):
    return Player.objects.create(first_name=name, last_name=position, position=position)


@pytest.fixture
def pool(db):
    """More than enough players of each position to fill a legal roster."""
    return {
        position: [make_player(position, f"P{i}") for i in range(7)]
        for position in Player.Position.values
    }


def fill(roster, pool, guards=5, forwards=5, centers=2, price=1_000_000):
    counts = {"G": guards, "F": forwards, "C": centers}
    for position, count in counts.items():
        for player in pool[position][:count]:
            services.buy(roster, player, price)


# --- completeness ------------------------------------------------------------


def test_a_new_roster_is_incomplete(roster):
    assert not roster.is_complete
    assert "15 more players" in roster.missing()


def test_missing_names_each_position_shortfall(roster, pool):
    fill(roster, pool, guards=2, forwards=0, centers=0)
    gaps = roster.missing()
    assert "3 more Guards" in gaps
    assert "5 more Forwards" in gaps
    assert "2 more Centers" in gaps


def test_a_legal_roster_is_complete(roster, pool):
    # 5G + 5F + 2C is only 12; the last three are the flex slots.
    fill(roster, pool, guards=6, forwards=6, centers=3)
    assert roster.player_count == 15
    assert roster.missing() == []
    assert roster.is_complete


def test_the_flex_slots_accept_any_position(roster, pool):
    fill(roster, pool, guards=5, forwards=5, centers=5)
    assert roster.is_complete


def test_position_minimums_still_bind_at_fifteen_players(roster, pool):
    # Fifteen players, but only one center.
    fill(roster, pool, guards=7, forwards=7, centers=1)
    assert roster.player_count == 15
    assert not roster.is_complete
    assert "1 more Center" in roster.missing()


# --- buying ------------------------------------------------------------------


def test_buying_moves_cash_and_adds_the_player(roster, pool):
    player = pool["G"][0]
    txn = services.buy(roster, player, 5_000_000)

    assert roster.cash == STARTING_CASH - 5_000_000
    assert player in roster.current_players
    assert txn.kind == Transaction.Kind.BUY
    assert txn.cash_delta == Decimal("-5000000")


def test_cannot_buy_what_you_cannot_afford(roster, pool):
    with pytest.raises(ValidationError, match="Not enough cash"):
        services.buy(roster, pool["G"][0], 61_000_000)
    roster.refresh_from_db()
    assert roster.cash == STARTING_CASH


def test_a_failed_buy_leaves_nothing_behind(roster, pool):
    with pytest.raises(ValidationError):
        services.buy(roster, pool["G"][0], 61_000_000)
    assert RosterPlayer.objects.count() == 0
    assert Transaction.objects.count() == 0


def test_cannot_buy_the_same_player_twice(roster, pool):
    services.buy(roster, pool["G"][0], 1_000_000)
    with pytest.raises(ValidationError, match="already on the roster"):
        services.buy(roster, pool["G"][0], 1_000_000)


def test_cannot_exceed_fifteen_players(roster, pool):
    fill(roster, pool, guards=5, forwards=5, centers=5)
    with pytest.raises(ValidationError, match="is full"):
        services.buy(roster, pool["G"][6], 1_000_000)


# --- selling -----------------------------------------------------------------


def test_selling_returns_cash_and_keeps_the_history(roster, pool):
    player = pool["F"][0]
    services.buy(roster, player, 4_000_000)
    services.sell(roster, player, 6_000_000)

    assert roster.cash == STARTING_CASH + 2_000_000
    assert player not in roster.current_players
    membership = RosterPlayer.objects.get(roster=roster, player=player)
    assert membership.removed_at is not None
    assert not membership.is_current


def test_cannot_sell_someone_you_do_not_have(roster, pool):
    with pytest.raises(ValidationError, match="is not on the roster"):
        services.sell(roster, pool["C"][0], 1_000_000)


def test_rebuying_a_sold_player_starts_a_second_spell(roster, pool):
    player = pool["G"][0]
    services.buy(roster, player, 1_000_000)
    services.sell(roster, player, 1_000_000)
    services.buy(roster, player, 2_000_000)

    assert RosterPlayer.objects.filter(roster=roster, player=player).count() == 2
    assert player in roster.current_players


# --- trading -----------------------------------------------------------------


def test_a_trade_is_one_transaction_not_two(roster, pool):
    out_player, in_player = pool["G"][0], pool["F"][0]
    services.buy(roster, out_player, 3_000_000)
    Transaction.objects.all().delete()

    txn = services.trade(roster, out_player, in_player, price_out=4_000_000, price_in=5_000_000)

    assert Transaction.objects.count() == 1
    assert txn.kind == Transaction.Kind.TRADE
    assert txn.cash_delta == Decimal("-1000000")
    assert roster.cash == STARTING_CASH - 3_000_000 - 1_000_000
    assert in_player in roster.current_players
    assert out_player not in roster.current_players


def test_a_trade_can_be_afforded_out_of_the_proceeds(roster, pool):
    """Selling for 50m and buying for 55m works with only 10m in hand."""
    out_player, in_player = pool["G"][0], pool["F"][0]
    services.buy(roster, out_player, 50_000_000)
    assert roster.cash == Decimal("10000000")

    services.trade(roster, out_player, in_player, price_out=50_000_000, price_in=55_000_000)
    assert roster.cash == Decimal("5000000")


def test_a_trade_that_does_not_balance_is_rejected(roster, pool):
    out_player, in_player = pool["G"][0], pool["F"][0]
    services.buy(roster, out_player, 55_000_000)

    with pytest.raises(ValidationError, match="Not enough cash"):
        services.trade(roster, out_player, in_player, price_out=1_000_000, price_in=40_000_000)

    roster.refresh_from_db()
    assert out_player in roster.current_players
    assert in_player not in roster.current_players


# --- constraints -------------------------------------------------------------


def test_transaction_kind_is_derived_from_the_players(roster, pool):
    buy = services.buy(roster, pool["G"][0], 1_000_000)
    sell = services.sell(roster, pool["G"][0], 1_000_000)
    assert buy.kind == Transaction.Kind.BUY
    assert sell.kind == Transaction.Kind.SELL


def test_cash_cannot_be_pushed_negative_even_from_the_shell(roster):
    roster.cash = Decimal("-1")
    with pytest.raises(IntegrityError):
        roster.save()


def test_only_one_season_can_be_current(season):
    with pytest.raises(IntegrityError):
        Season.objects.create(
            label="2026-27",
            starts_on=dt.date(2026, 10, 20),
            ends_on=dt.date(2027, 4, 11),
            is_current=True,
        )


def test_two_rosters_cannot_share_a_name_in_one_season(roster, season, manager):
    with pytest.raises(IntegrityError):
        Roster.objects.create(manager=manager, season=season, name=roster.name)
