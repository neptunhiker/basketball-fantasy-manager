"""Whether a past move may be moved or undone: the replay in `apps.fantasy.ledger`.

Each test makes a short real history with the services, then asks what a
correction would do. The interesting answers are the refusals that come from
*later* in the history -- a correction is only safe if everything that
happened after it still could have.
"""

import datetime as dt
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.fantasy import ledger
from apps.fantasy.models import RosterPlayer, TradeGrant, Transaction

from .move_correction_factories import M, World, at

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(manager):
    return World(manager)


def problems(correction):
    return " | ".join(correction.problems)


# --- which moves may be corrected -----------------------------------------------


def test_only_trades_and_bought_or_sold_trades_are_correctable(world):
    a, b = world.player(2), world.player(2)
    signing = world.sign(a)
    trade = world.trade(a, b, world.week1(2))
    bought = world.buy_trade(world.week1(3))
    grant = world.roster.transactions.get(kind=Transaction.Kind.GRANT_TRADE)

    assert ledger.is_correctable(trade, world.season)
    assert ledger.is_correctable(bought, world.season)
    assert not ledger.is_correctable(signing, world.season)
    assert not ledger.is_correctable(grant, world.season)


def test_nothing_is_correctable_once_the_season_is_over(world):
    a, b = world.player(2), world.player(2)
    world.sign(a)
    trade = world.trade(a, b, world.week1(2))

    after_the_end = world.season.ends_on + dt.timedelta(days=1)
    assert not ledger.is_correctable(trade, world.season, today=after_the_end)


def test_a_signing_cannot_be_previewed_either(world):
    a = world.player(2)
    signing = world.sign(a)

    correction = ledger.preview_undo(world.reload(), signing)
    assert not correction.ok
    assert "can no longer be changed" in problems(correction)


# --- moving a trade: what it changes --------------------------------------------


def test_a_trade_moved_within_its_salary_week_keeps_its_prices(world):
    a, b = world.player(5), world.player(3)
    world.sign(a)
    trade = world.trade(a, b, world.week1(2))

    correction = ledger.preview_move(world.reload(), trade, world.week1(4))

    assert correction.ok, problems(correction)
    assert not correction.repriced
    assert correction.occurred_at_after == at(world.week1(4))
    assert correction.price_out_after == 5 * M
    assert correction.price_in_after == 3 * M
    assert correction.cash_change == 0
    assert correction.trades_change == 0


def test_the_clock_time_is_kept(world):
    a, b = world.player(2), world.player(2)
    world.sign(a)
    trade = world.trade(a, b, world.week1(2), hour=9)
    trade.occurred_at = trade.occurred_at.replace(minute=37, second=12)
    trade.save()
    RosterPlayer.objects.filter(player=a).update(removed_at=trade.occurred_at)
    RosterPlayer.objects.filter(player=b).update(added_at=trade.occurred_at)

    correction = ledger.preview_move(world.reload(), trade, world.week1(4))

    assert correction.ok, problems(correction)
    local = timezone.localtime(correction.occurred_at_after)
    assert (local.date(), local.hour, local.minute, local.second) == (world.week1(4), 9, 37, 12)


def test_a_trade_moved_across_a_salary_update_is_repriced(world):
    a, b = world.player(5), world.player(3)
    world.sign(a)
    trade = world.trade(a, b, world.week1(2))
    # The update a week after the first: both prices move.
    world.snapshot(a, at(world.week2(0), 22, 15), "5.5")
    world.snapshot(b, at(world.week2(0), 22, 15), "2.5")

    correction = ledger.preview_move(world.reload(), trade, world.week2(2))

    assert correction.ok, problems(correction)
    assert correction.repriced
    assert correction.repriced_by == at(world.week2(0), 22)
    assert correction.price_out_after == Decimal("5.5") * M
    assert correction.price_in_after == Decimal("2.5") * M
    assert correction.cash_delta_after == 3 * M
    # Was +2M (5 - 3), is +3M now.
    assert correction.cash_change == 1 * M


def test_a_sunday_afternoon_still_trades_at_the_old_salaries(world):
    """The batch runs at 22:00, before that evening's update takes effect."""
    a, b = world.player(5), world.player(3)
    world.sign(a)
    trade = world.trade(a, b, world.week1(2))
    world.snapshot(a, at(world.week2(0), 22, 15), "9")
    world.snapshot(b, at(world.week2(0), 22, 15), "1")

    correction = ledger.preview_move(world.reload(), trade, world.week2(0))

    assert correction.ok, problems(correction)
    assert not correction.repriced
    assert correction.cash_delta_after == trade.cash_delta


def test_a_move_across_an_update_without_a_salary_on_record_is_refused(world):
    a, b = world.player(5), world.player(3)
    world.sign(a)
    trade = world.trade(a, b, world.week1(2))
    world.snapshot(a, at(world.week2(0), 22, 15), "5.5")  # nothing for b

    correction = ledger.preview_move(world.reload(), trade, world.week2(2))

    assert not correction.ok
    assert f"No salary on record for {b}" in problems(correction)


def test_a_week_old_snapshot_does_not_stand_in_for_the_new_week(world):
    a, b = world.player(5), world.player(3)
    world.sign(a)
    trade = world.trade(a, b, world.week1(2))
    world.snapshot(a, at(world.week1(1), 22, 15), "5")
    world.snapshot(b, at(world.week1(1), 22, 15), "3")
    world.snapshot(a, at(world.week2(0), 22, 15), "5.5")

    correction = ledger.preview_move(world.reload(), trade, world.week2(2))

    assert not correction.ok
    assert f"No salary on record for {b}" in problems(correction)


# --- moving a trade: what refuses it --------------------------------------------


def test_the_same_day_is_refused(world):
    a, b = world.player(2), world.player(2)
    world.sign(a)
    trade = world.trade(a, b, world.week1(2))

    correction = ledger.preview_move(world.reload(), trade, world.week1(2))
    assert "already on this one" in problems(correction)


def test_a_day_in_the_future_is_refused(world):
    a, b = world.player(2), world.player(2)
    world.sign(a)
    trade = world.trade(a, b, world.week1(2))

    tomorrow = timezone.localdate() + dt.timedelta(days=1)
    correction = ledger.preview_move(world.reload(), trade, tomorrow)
    assert "not happened yet" in problems(correction)


def test_a_trade_cannot_be_moved_before_the_season_started(world):
    a, b = world.player(2), world.player(2)
    world.sign(a)
    trade = world.trade(a, b, world.week1(2))

    before = world.season.starts_on - dt.timedelta(days=1)
    correction = ledger.preview_move(world.reload(), trade, before)
    assert "while the season is running" in problems(correction)


def test_a_bought_trade_cannot_be_moved_before_the_market_opened(world):
    bought = world.buy_trade(world.week1(2))

    correction = ledger.preview_move(world.reload(), bought, world.week1(-2))
    assert "while the trade market is open" in problems(correction)


def test_a_trade_moved_before_the_free_trade_it_used_has_none(manager):
    world = World(manager, start_trades=0)
    TradeGrant.objects.create(
        season=world.season, granted_at=at(world.week1(3), 22), trades=1, label="Helpside"
    )
    a, b = world.player(2), world.player(2)
    world.sign(a)
    trade = world.trade(a, b, world.week1(4))

    correction = ledger.preview_move(world.reload(), trade, world.week1(2))

    assert not correction.ok
    assert "This move on" in problems(correction)
    assert "no trade available" in problems(correction)


def test_a_trade_moved_later_can_starve_a_later_one_of_cash(manager):
    world = World(manager, starting_cash=Decimal("10000000"))
    expensive, cheap = world.player(8), world.player(1)
    g, star = world.player(2), world.player(9)
    world.sign(expensive)
    world.sign(g)
    # Selling 8M for 1M funds the 9M star two days later.
    funding = world.trade(expensive, cheap, world.week1(1))
    world.trade(g, star, world.week1(3))

    correction = ledger.preview_move(world.reload(), funding, world.week1(5))

    assert not correction.ok
    assert f"Trade {g.last_name} → {star.last_name}" in problems(correction)
    assert "would leave cash at" in problems(correction)


def test_a_trade_cannot_move_past_the_day_its_incoming_player_left(world):
    a, b, c = world.player(2), world.player(2), world.player(2)
    world.sign(a)
    first = world.trade(a, b, world.week1(1))
    world.trade(b, c, world.week1(3))

    correction = ledger.preview_move(world.reload(), first, world.week1(5))

    assert not correction.ok
    assert f"{b} would leave the roster before joining it" in problems(correction)


def test_a_trade_cannot_move_past_the_day_its_outgoing_player_came_back(world):
    a, b, c = world.player(2), world.player(2), world.player(2)
    world.sign(a)
    world.sign(c)
    first = world.trade(a, b, world.week1(1))
    world.trade(c, a, world.week1(3))

    correction = ledger.preview_move(world.reload(), first, world.week1(5))

    assert not correction.ok
    assert f"{a} would be on the roster twice at once" in problems(correction)


def test_a_trade_cannot_move_before_its_outgoing_player_arrived(world):
    x, a, b = world.player(2), world.player(2), world.player(2)
    world.sign(x)
    world.trade(x, a, world.week1(3))
    second = world.trade(a, b, world.week1(5))

    correction = ledger.preview_move(world.reload(), second, world.week1(1))

    assert not correction.ok
    assert f"{a} would leave the roster before joining it" in problems(correction)


def test_a_trade_whose_spells_were_edited_by_hand_is_refused(world):
    a, b = world.player(2), world.player(2)
    world.sign(a)
    trade = world.trade(a, b, world.week1(2))
    RosterPlayer.objects.filter(player=b).update(added_at=at(world.week1(2), 15))

    correction = ledger.preview_move(world.reload(), trade, world.week1(4))
    assert "records do not line up" in problems(correction)


# --- undoing --------------------------------------------------------------------


def test_undoing_a_trade_gives_back_the_trade_and_the_money(world):
    a, b = world.player(5), world.player(3)
    world.sign(a)
    trade = world.trade(a, b, world.week1(2))
    roster = world.reload()

    correction = ledger.preview_undo(roster, trade)

    assert correction.ok, problems(correction)
    assert correction.cash_change == -2 * M
    assert correction.trades_change == 1


def test_a_trade_cannot_be_undone_once_its_player_moved_on(world):
    a, b, c = world.player(2), world.player(2), world.player(2)
    world.sign(a)
    first = world.trade(a, b, world.week1(1))
    world.trade(b, c, world.week1(3))

    correction = ledger.preview_undo(world.reload(), first)

    assert not correction.ok
    assert f"{b} has left the roster again since" in problems(correction)
    assert "Undo that move first" in problems(correction)


def test_a_trade_cannot_be_undone_once_its_outgoing_player_is_back(world):
    a, b, c = world.player(2), world.player(2), world.player(2)
    world.sign(a)
    world.sign(c)
    first = world.trade(a, b, world.week1(1))
    world.trade(c, a, world.week1(3))

    correction = ledger.preview_undo(world.reload(), first)

    assert not correction.ok
    assert f"{a} would be on the roster twice at once" in problems(correction)


def test_undoing_a_trade_that_funded_a_later_one_is_refused(manager):
    world = World(manager, starting_cash=Decimal("10000000"))
    expensive, cheap = world.player(8), world.player(1)
    g, star = world.player(2), world.player(9)
    world.sign(expensive)
    world.sign(g)
    funding = world.trade(expensive, cheap, world.week1(1))
    world.trade(g, star, world.week1(3))

    correction = ledger.preview_undo(world.reload(), funding)

    assert not correction.ok
    assert "would leave cash at" in problems(correction)


def test_undoing_a_bought_trade_that_was_used_is_refused(manager):
    world = World(manager, start_trades=0)
    a, b = world.player(2), world.player(2)
    world.sign(a)
    bought = world.buy_trade(world.week1(1))
    world.trade(a, b, world.week1(2))

    correction = ledger.preview_undo(world.reload(), bought)

    assert not correction.ok
    assert "no trade available" in problems(correction)


def test_undoing_an_unused_bought_trade_refunds_it(manager):
    world = World(manager, start_trades=0)
    bought = world.buy_trade(world.week1(1))

    correction = ledger.preview_undo(world.reload(), bought)

    assert correction.ok, problems(correction)
    assert correction.cash_change == Decimal("1500000")
    assert correction.trades_change == -1


def test_undoing_a_sold_trade_takes_the_money_back(world):
    sold = world.sell_trade(world.week1(1))

    correction = ledger.preview_undo(world.reload(), sold)

    assert correction.ok, problems(correction)
    assert correction.cash_change == -1 * M
    assert correction.trades_change == 1


def test_a_sold_trade_cannot_move_before_the_roster_had_one(manager):
    world = World(manager, start_trades=0)
    TradeGrant.objects.create(
        season=world.season, granted_at=at(world.week1(2), 22), trades=1, label="Helpside"
    )
    world.reload()
    sold = world.sell_trade(world.week1(4))

    correction = ledger.preview_move(world.reload(), sold, world.week1(1))
    assert "would sell a trade the roster does not have" in problems(correction)


# --- the ledger as a whole ------------------------------------------------------


def test_a_roster_whose_cash_does_not_add_up_cannot_be_corrected(world):
    a, b = world.player(2), world.player(2)
    world.sign(a)
    trade = world.trade(a, b, world.week1(2))
    world.roster.__class__.objects.filter(pk=world.roster.pk).update(cash=1)

    correction = ledger.preview_move(world.reload(), trade, world.week1(4))
    assert "records do not add up" in problems(correction)


def test_trades_the_ledger_does_not_explain_count_as_an_opening_balance(manager):
    """Rosters built when every team started with two trades still have them."""
    world = World(manager, start_trades=0)
    world.roster.__class__.objects.filter(pk=world.roster.pk).update(trades_available=2)
    a, b = world.player(2), world.player(2)
    world.sign(a)
    trade = world.trade(a, b, world.week1(3))

    correction = ledger.preview_move(world.reload(), trade, world.week1(1))
    assert correction.ok, problems(correction)


def test_an_old_problem_does_not_block_an_unrelated_correction(world):
    a, b, c, d = (world.player(2) for _ in range(4))
    world.sign(a)
    world.sign(c)
    world.trade(a, b, world.week1(1))
    later = world.trade(c, d, world.week1(3))
    # A hand-made inconsistency elsewhere: `a` back on the roster without a move.
    RosterPlayer.objects.create(roster=world.roster, player=a, added_at=at(world.week1(0)))

    correction = ledger.preview_move(world.reload(), later, world.week1(5))
    assert correction.ok, problems(correction)


# --- position minimums: warned about, not refused ---------------------------------


def test_moving_a_trade_that_leaves_a_position_short_earlier_warns(world):
    guards = world.squad()[:5]
    center = world.player(2, "C")
    trade = world.trade(guards[0], center, world.week1(4))

    correction = ledger.preview_move(world.reload(), trade, world.week1(1))

    assert correction.ok, problems(correction)
    assert correction.warnings
    assert "1 more Guard" in correction.warnings[0]


def test_a_trade_that_keeps_the_positions_does_not_warn(world):
    guards = world.squad()[:5]
    guard = world.player(2, "G")
    trade = world.trade(guards[0], guard, world.week1(4))

    correction = ledger.preview_move(world.reload(), trade, world.week1(1))

    assert correction.ok, problems(correction)
    assert correction.warnings == []


# --- the date picker --------------------------------------------------------------


def test_the_date_picker_runs_from_the_season_start_to_today(world):
    a, b = world.player(2), world.player(2)
    world.sign(a)
    trade = world.trade(a, b, world.week1(2), hour=0)

    first, last = ledger.day_range(trade, world.season)
    assert first == world.season.starts_on
    assert last == timezone.localdate()


def test_the_date_picker_stops_yesterday_when_the_clock_time_is_still_ahead(world):
    a, b = world.player(2), world.player(2)
    world.sign(a)
    trade = world.trade(a, b, world.week1(2))
    now = at(timezone.localdate(), 10)  # the move was made at 14:00

    _, last = ledger.day_range(trade, world.season, now=now)
    assert last == timezone.localdate() - dt.timedelta(days=1)


def test_a_bought_trade_can_only_be_dated_from_the_market_opening(world):
    bought = world.buy_trade(world.week1(2), hour=23)

    first, _ = ledger.day_range(bought, world.season)
    # The market opened at 22:00 on that Sunday; 23:00 that evening fits.
    assert first == world.sunday
