"""Writing a correction: `services.move_transaction` and `undo_transaction`.

`test_move_correction_ledger` covers whether a correction may happen. These
cover what one writes -- every record of the move changed together, the
ledger still adding up afterwards, a log row for each -- and that a refused
one writes nothing at all.
"""

import datetime as dt
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from apps.fantasy import services
from apps.fantasy.models import Roster, RosterPlayer, Transaction, TransactionCorrection
from apps.fantasy.timeline import roster_timeline

from .move_correction_factories import M, World, at

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(manager):
    return World(manager)


def ledger_adds_up(roster):
    roster.refresh_from_db()
    moves = list(roster.transactions.all())
    cash = roster.starting_cash + sum((move.cash_delta for move in moves), Decimal("0"))
    return cash == roster.cash


def spell(roster, player, **filters):
    return RosterPlayer.objects.get(roster=roster, player=player, **filters)


# --- moving -----------------------------------------------------------------------


def test_moving_a_trade_moves_all_three_records(world, user):
    a, b = world.player(5), world.player(3)
    world.sign(a)
    trade = world.trade(a, b, world.week1(2))
    cash = world.reload().cash

    log = services.move_transaction(world.roster, trade, world.week1(4), user=user)

    moment = at(world.week1(4))
    trade.refresh_from_db()
    assert trade.occurred_at == moment
    assert spell(world.roster, a).removed_at == moment
    assert spell(world.roster, b).added_at == moment
    assert world.roster.cash == cash  # the passed instance is refreshed
    assert ledger_adds_up(world.roster)

    assert log.action == TransactionCorrection.Action.MOVED
    assert log.transaction_id == trade.pk
    assert log.occurred_at_before == at(world.week1(2))
    assert log.occurred_at_after == moment
    assert log.made_by == user
    assert log.player_out == a and log.player_in == b


def test_moving_a_trade_across_an_update_reprices_it(world):
    a, b = world.player(5), world.player(3)
    world.sign(a)
    trade = world.trade(a, b, world.week1(2))
    world.snapshot(a, at(world.week2(0), 22, 15), "5.5")
    world.snapshot(b, at(world.week2(0), 22, 15), "2.5")
    cash = world.reload().cash

    log = services.move_transaction(world.roster, trade, world.week2(2))

    trade.refresh_from_db()
    assert (trade.price_out, trade.price_in, trade.cash_delta) == (
        Decimal("5.5") * M,
        Decimal("2.5") * M,
        3 * M,
    )
    assert world.roster.cash == cash + 1 * M
    assert ledger_adds_up(world.roster)
    assert (log.cash_delta_before, log.cash_delta_after) == (2 * M, 3 * M)
    assert (log.price_out_before, log.price_out_after) == (5 * M, Decimal("5.5") * M)


def test_moving_a_bought_trade_changes_only_its_date(world):
    bought = world.buy_trade(world.week1(2))
    roster = world.reload()
    cash, trades = roster.cash, roster.trades_available

    services.move_transaction(roster, bought, world.week1(5))

    bought.refresh_from_db()
    assert bought.occurred_at == at(world.week1(5))
    assert (roster.cash, roster.trades_available) == (cash, trades)


def test_a_refused_move_writes_nothing(world):
    a, b, c = world.player(2), world.player(2), world.player(2)
    world.sign(a)
    first = world.trade(a, b, world.week1(1))
    world.trade(b, c, world.week1(3))
    world.reload()

    with pytest.raises(ValidationError, match="before joining it"):
        services.move_transaction(world.roster, first, world.week1(5))

    first.refresh_from_db()
    assert first.occurred_at == at(world.week1(1))
    assert spell(world.roster, b).added_at == at(world.week1(1))
    assert not TransactionCorrection.objects.exists()


def test_the_same_move_twice_is_refused_the_second_time(world):
    a, b = world.player(2), world.player(2)
    world.sign(a)
    trade = world.trade(a, b, world.week1(2))

    services.move_transaction(world.roster, trade, world.week1(4))
    with pytest.raises(ValidationError, match="already on this one"):
        services.move_transaction(world.roster, trade, world.week1(4))
    assert TransactionCorrection.objects.count() == 1


def test_a_move_of_another_roster_is_not_found(world, other_manager):
    theirs = Roster.objects.create(manager=other_manager, season=world.season, name="Rivals")
    a, b = world.player(2), world.player(2)
    services.buy(theirs, a, a.current_salary, occurred_at=at(world.season.starts_on))
    their_trade = services.trade(
        theirs, a, b, a.current_salary, b.current_salary, occurred_at=at(world.week1(2))
    )

    with pytest.raises(Transaction.DoesNotExist):
        services.move_transaction(world.roster, their_trade, world.week1(4))
    their_trade.refresh_from_db()
    assert their_trade.occurred_at == at(world.week1(2))


def test_a_moved_trade_is_still_one_link_on_the_timeline(world):
    players = world.squad()
    newcomer = world.player(2, "G")
    trade = world.trade(players[0], newcomer, world.week1(2))

    services.move_transaction(world.roster, trade, world.week1(4))

    timeline = roster_timeline(world.reload())
    assert timeline.trade_count == 1
    lanes = [spot["stints"] for spot in timeline.spots]
    traded = next(lane for lane in lanes if len(lane) == 2)
    assert traded[0]["name"] == players[0].full_name
    assert traded[1]["name"] == newcomer.full_name
    assert traded[1]["start"] == world.week1(4).isoformat()
    assert traded[1]["came"] is not None
    assert len(timeline.spots) == 15


# --- undoing ----------------------------------------------------------------------


def test_undoing_a_trade_restores_the_roster(world, user):
    a, b = world.player(5), world.player(3)
    world.sign(a)
    roster = world.reload()
    cash, trades = roster.cash, roster.trades_available
    trade = world.trade(a, b, world.week1(2))

    log = services.undo_transaction(world.roster, trade, user=user)

    assert spell(world.roster, a).removed_at is None
    assert not RosterPlayer.objects.filter(roster=world.roster, player=b).exists()
    assert not Transaction.objects.filter(pk=trade.pk).exists()
    assert (world.roster.cash, world.roster.trades_available) == (cash, trades)
    assert ledger_adds_up(world.roster)

    assert log.action == TransactionCorrection.Action.UNDONE
    assert log.transaction_id == trade.pk
    assert log.occurred_at_after is None
    assert (log.price_out_before, log.price_in_before) == (5 * M, 3 * M)
    assert log.made_by == user


def test_undoing_a_bought_trade_refunds_it(manager):
    world = World(manager, start_trades=0)
    roster = world.reload()
    cash = roster.cash
    bought = world.buy_trade(world.week1(1))

    services.undo_transaction(world.roster, bought)

    assert (world.roster.cash, world.roster.trades_available) == (cash, 0)
    assert ledger_adds_up(world.roster)


def test_undoing_a_sold_trade_returns_it(world):
    services.pay_due_trade_grants([world.roster])
    roster = world.reload()
    cash, trades = roster.cash, roster.trades_available
    sold = world.sell_trade(world.week1(1))

    services.undo_transaction(world.roster, sold)

    assert (world.roster.cash, world.roster.trades_available) == (cash, trades)
    assert ledger_adds_up(world.roster)


def test_a_refused_undo_writes_nothing(world):
    a, b, c = world.player(2), world.player(2), world.player(2)
    world.sign(a)
    first = world.trade(a, b, world.week1(1))
    world.trade(b, c, world.week1(3))
    roster = world.reload()
    cash, trades = roster.cash, roster.trades_available

    with pytest.raises(ValidationError, match="Undo that move first"):
        services.undo_transaction(world.roster, first)

    assert Transaction.objects.filter(pk=first.pk).exists()
    assert spell(world.roster, b, added_at=at(world.week1(1))).removed_at is not None
    world.reload()
    assert (world.roster.cash, world.roster.trades_available) == (cash, trades)
    assert not TransactionCorrection.objects.exists()


def test_trades_are_undone_newest_first(world):
    a, b, c = world.player(2), world.player(2), world.player(2)
    world.sign(a)
    first = world.trade(a, b, world.week1(1))
    second = world.trade(b, c, world.week1(3))

    services.undo_transaction(world.roster, second)
    services.undo_transaction(world.roster, first)

    open_spells = RosterPlayer.objects.open().filter(roster=world.roster)
    assert [s.player for s in open_spells] == [a]
    assert ledger_adds_up(world.roster)
    assert TransactionCorrection.objects.count() == 2


def test_free_trades_due_are_paid_before_judging(manager):
    """The lock pays what has fallen due, so the judgement sees today's count.

    The bought trade was spent on a trade. Undoing it is only possible because
    a weekly free trade fell due before that trade -- one the roster has not
    been paid yet when the undo is asked for.
    """
    world = World(manager, start_trades=0)
    a, b = world.player(2), world.player(2)
    world.sign(a)
    bought = world.buy_trade(world.week1(1))
    world.trade(a, b, world.week1(2))
    world.season.weekly_trades_from = at(world.week1(0) - dt.timedelta(days=7), 22)
    world.season.save()
    assert not world.roster.transactions.filter(kind=Transaction.Kind.GRANT_TRADE).exists()

    services.undo_transaction(world.roster, bought)

    assert world.roster.transactions.filter(kind=Transaction.Kind.GRANT_TRADE).exists()
    assert ledger_adds_up(world.roster)
