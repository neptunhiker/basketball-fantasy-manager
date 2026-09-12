"""The transaction log as a ledger.

`services` already had one transaction per move; what is tested here is whether
that log is *complete* -- whether it records enough to answer "what did this
cost" afterwards, whether it can be made to disagree with `Roster.cash`, and
whether the screen that shows it checks its own arithmetic.
"""

import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse

from apps.fantasy import services
from apps.fantasy.admin import TransactionAdmin
from apps.fantasy.models import STARTING_CASH, Roster, Season, Transaction
from apps.nba.models import Player

User = get_user_model()


@pytest.fixture
def season(db):
    return Season.objects.create(
        label="2026-27",
        starts_on=dt.date(2026, 10, 20),
        ends_on=dt.date(2027, 4, 11),
        is_current=True,
    )


@pytest.fixture
def roster(season, manager):
    return Roster.objects.create(manager=manager, season=season, name="Roster 1")


@pytest.fixture
def signed_in(client, user, password, db):
    client.login(email=user.email, password=password)
    return client


def make_player(last_name, salary, position="G"):
    return Player.objects.create(
        first_name="A", last_name=last_name, position=position, current_salary=Decimal(salary)
    )


def history_url(roster):
    return reverse("fantasy:roster-history", args=[roster.pk])


# --- what each move writes down -----------------------------------------------


def test_a_buy_records_what_was_paid(roster):
    player = make_player("Bought", "3000000")
    move = services.buy(roster, player, Decimal("3000000"))

    assert move.kind == Transaction.Kind.BUY
    assert move.price_in == Decimal("3000000")
    assert move.price_out is None
    assert move.cash_delta == Decimal("-3000000")
    assert move.is_priced


def test_a_sale_records_what_was_received(roster):
    player = make_player("Sold", "3000000")
    services.buy(roster, player, Decimal("3000000"))
    move = services.sell(roster, player, Decimal("2500000"))

    assert move.kind == Transaction.Kind.SELL
    assert move.price_out == Decimal("2500000")
    assert move.price_in is None
    assert move.cash_delta == Decimal("2500000")
    assert move.is_priced


def test_a_trade_records_both_sides_not_just_the_net(roster):
    """The gap this work closed: `cash_delta` alone cannot be split back in two."""
    out_player = make_player("Outbound", "11450000")
    in_player = make_player("Inbound", "650000")
    services.buy(roster, out_player, Decimal("11450000"))

    move = services.trade(
        roster,
        out_player,
        in_player,
        price_out=Decimal("11450000"),
        price_in=Decimal("650000"),
    )

    assert move.kind == Transaction.Kind.TRADE
    assert move.price_out == Decimal("11450000")
    assert move.price_in == Decimal("650000")
    assert move.cash_delta == Decimal("10800000")
    # Two records of the same money, and they agree.
    assert move.implied_cash_delta == move.cash_delta


def test_the_prices_survive_a_reload(roster):
    """Written to the database, not just set on the returned instance."""
    player = make_player("Bought", "1000000")
    move = services.buy(roster, player, Decimal("1000000"))

    assert Transaction.objects.get(pk=move.pk).price_in == Decimal("1000000")


# --- what the log refuses to hold ---------------------------------------------


def test_a_cash_delta_that_contradicts_the_prices_is_invalid(roster):
    player = make_player("Wrong", "1000000")
    move = Transaction(
        roster=roster,
        occurred_at=dt.datetime(2026, 10, 25, 12, tzinfo=dt.UTC),
        player_in=player,
        price_in=Decimal("1000000"),
        cash_delta=Decimal("-500000"),
    )
    with pytest.raises(ValidationError, match="does not match the prices"):
        move.full_clean()


def test_a_negative_price_is_refused_by_the_database(roster):
    player = make_player("Negative", "1000000")
    with pytest.raises(IntegrityError), transaction.atomic():
        Transaction.objects.create(
            roster=roster,
            occurred_at=dt.datetime(2026, 10, 25, 12, tzinfo=dt.UTC),
            player_in=player,
            price_in=Decimal("-1"),
            cash_delta=Decimal("1"),
        )


def test_a_price_without_the_player_it_paid_for_is_refused(roster):
    """A price on a side that moved nobody means nothing, legacy row or not."""
    player = make_player("Half", "1000000")
    with pytest.raises(IntegrityError), transaction.atomic():
        Transaction.objects.create(
            roster=roster,
            occurred_at=dt.datetime(2026, 10, 25, 12, tzinfo=dt.UTC),
            player_in=player,
            price_in=Decimal("1000000"),
            price_out=Decimal("500000"),
            cash_delta=Decimal("-1000000"),
        )


def test_an_unpriced_row_is_still_allowed(roster):
    """An unknown price has to stay a legal state: rows written before the
    prices existed hold only the net, and nothing can split it back in two."""
    player = make_player("Old", "1000000")
    move = Transaction.objects.create(
        roster=roster,
        occurred_at=dt.datetime(2026, 10, 25, 12, tzinfo=dt.UTC),
        player_in=player,
        cash_delta=Decimal("-1000000"),
    )

    assert not move.is_priced
    move.full_clean()  # no price to disagree with the delta


# --- the cost basis the prices unlock -----------------------------------------


def test_a_bought_player_refunds_the_purchase_price(roster):
    player = make_player("Purchased", "3000000")
    services.buy(roster, player, Decimal("3000000"))
    player.current_salary = Decimal("5000000")
    player.save(update_fields=["current_salary"])

    assert services.amount_paid_for(roster, player) == Decimal("3000000")


def test_a_traded_in_player_refunds_what_the_trade_valued_him_at(roster):
    """Before the prices were recorded this had to fall back to today's salary,
    because a trade's net delta says nothing about either side on its own."""
    out_player = make_player("Outbound", "4000000")
    in_player = make_player("Inbound", "1500000")
    services.buy(roster, out_player, Decimal("4000000"))
    services.trade(
        roster,
        out_player,
        in_player,
        price_out=Decimal("4000000"),
        price_in=Decimal("1500000"),
    )
    in_player.current_salary = Decimal("9000000")
    in_player.save(update_fields=["current_salary"])

    assert services.amount_paid_for(roster, in_player) == Decimal("1500000")


def test_an_unpriced_arrival_falls_back_to_todays_salary(roster):
    player = make_player("Old", "2000000")
    Transaction.objects.create(
        roster=roster,
        occurred_at=dt.datetime(2026, 10, 25, 12, tzinfo=dt.UTC),
        player_in=player,
        cash_delta=Decimal("-1750000"),
    )

    assert services.amount_paid_for(roster, player) == Decimal("2000000")


def test_the_latest_arrival_is_the_one_that_counts(roster):
    """Bought, sold, bought again cheaper: the refund follows the second spell."""
    player = make_player("Returning", "5000000")
    services.buy(roster, player, Decimal("5000000"))
    services.sell(roster, player, Decimal("5000000"))
    services.buy(roster, player, Decimal("2000000"))

    assert services.amount_paid_for(roster, player) == Decimal("2000000")


# --- the ledger adds up -------------------------------------------------------


def test_every_move_leaves_a_row(roster):
    first = make_player("One", "1000000")
    second = make_player("Two", "2000000")
    third = make_player("Three", "1500000")

    services.buy(roster, first, Decimal("1000000"))
    services.buy(roster, second, Decimal("2000000"))
    services.sell(roster, first, Decimal("1000000"))
    services.trade(roster, second, third, price_out=Decimal("2000000"), price_in=Decimal("1500000"))

    assert roster.transactions.count() == 4
    kinds = [move.kind for move in roster.transactions.order_by("occurred_at")]
    assert kinds == [
        Transaction.Kind.BUY,
        Transaction.Kind.BUY,
        Transaction.Kind.SELL,
        Transaction.Kind.TRADE,
    ]


def test_the_log_reconciles_to_the_balance(roster):
    """The invariant the whole ledger rests on: start plus every delta is cash."""
    players = [make_player(f"S{i}", f"{(i + 1) * 1000000}") for i in range(4)]
    for player in players[:3]:
        services.buy(roster, player, player.current_salary)
    services.sell(roster, players[0], Decimal("900000"))
    services.trade(
        roster, players[1], players[3], price_out=Decimal("2000000"), price_in=Decimal("4000000")
    )
    roster.refresh_from_db()

    ledger = STARTING_CASH + sum(move.cash_delta for move in roster.transactions.all())
    assert ledger == roster.cash


def test_a_refused_move_leaves_no_row(roster):
    player = make_player("Dear", "70000000")
    with pytest.raises(ValidationError):
        services.buy(roster, player, Decimal("70000000"))

    assert roster.transactions.count() == 0


# --- the Verlauf page ---------------------------------------------------------


def test_the_history_page_lists_every_move(signed_in, roster):
    out_player = make_player("Outbound", "4000000")
    in_player = make_player("Inbound", "1500000")
    services.buy(roster, out_player, Decimal("4000000"))
    services.trade(
        roster, out_player, in_player, price_out=Decimal("4000000"), price_in=Decimal("1500000")
    )

    response = signed_in.get(history_url(roster))
    body = response.content.decode()

    assert response.status_code == 200
    assert "A Outbound" in body
    assert "A Inbound" in body
    assert "Buy" in body
    assert "Trade" in body


def test_the_page_shows_the_balance_each_move_left(signed_in, roster):
    services.buy(roster, make_player("One", "10000000"), Decimal("10000000"))
    services.buy(roster, make_player("Two", "5000000"), Decimal("5000000"))

    body = signed_in.get(history_url(roster)).content.decode()

    # 60 - 10 = 50, then 50 - 5 = 45, plus the Saisonstart row.
    assert "$50.00M" in body
    assert "$45.00M" in body
    assert "$60.00M" in body


def test_the_page_says_the_log_reconciles(signed_in, roster):
    services.buy(roster, make_player("One", "10000000"), Decimal("10000000"))

    response = signed_in.get(history_url(roster))
    body = response.content.decode()

    assert response.context["reconciles"] is True
    assert "comes to exactly the" in body
    assert "An entry is missing" not in body


def test_a_balance_that_does_not_match_the_log_is_called_out(signed_in, roster):
    """Unreachable through the service layer, which is exactly why it is worth a
    banner: if it ever shows, something wrote around the transactions."""
    services.buy(roster, make_player("One", "10000000"), Decimal("10000000"))
    Roster.objects.filter(pk=roster.pk).update(cash=Decimal("55000000"))

    response = signed_in.get(history_url(roster))

    assert response.context["reconciles"] is False
    assert "An entry is missing" in response.content.decode()


def test_the_page_admits_to_rows_without_prices(signed_in, roster):
    player = make_player("Old", "1000000")
    Transaction.objects.create(
        roster=roster,
        occurred_at=dt.datetime(2026, 10, 25, 12, tzinfo=dt.UTC),
        player_in=player,
        cash_delta=Decimal("-1000000"),
    )
    Roster.objects.filter(pk=roster.pk).update(cash=STARTING_CASH - Decimal("1000000"))

    response = signed_in.get(history_url(roster))

    assert response.context["unpriced"] == 1
    assert "before the individual prices were kept" in response.content.decode()


def test_an_empty_log_says_so(signed_in, roster):
    body = signed_in.get(history_url(roster)).content.decode()

    assert "No moves yet" in body


def test_the_build_page_links_to_the_history(signed_in, roster):
    body = signed_in.get(reverse("fantasy:roster-build", args=[roster.pk])).content.decode()

    assert history_url(roster) in body
    # The accessible name rather than the indented text: the link is a clock
    # icon below `sm`, and "History" also appears in the page's own copy.
    assert 'aria-label="History"' in body


def test_another_users_history_is_not_found(signed_in, season, other_manager):
    theirs = Roster.objects.create(manager=other_manager, season=season, name="Theirs")

    assert signed_in.get(history_url(theirs)).status_code == 404


def test_the_history_needs_a_login(client, roster):
    response = client.get(history_url(roster))

    assert response.status_code == 302
    assert "/login" in response["Location"] or "anmelden" in response["Location"]


def test_no_template_comment_leaks_into_the_page(signed_in, roster):
    """A `{# ... #}` comment wrapped over two lines renders as page text."""
    services.buy(roster, make_player("One", "1000000"), Decimal("1000000"))

    body = signed_in.get(history_url(roster)).content.decode()

    assert "{#" not in body
    assert "{%" not in body


# --- the log cannot be edited away -------------------------------------------


def test_the_admin_will_not_let_the_ledger_be_changed():
    """Editing or deleting a row would break the reconciliation above silently."""
    admin = TransactionAdmin(Transaction, None)

    assert admin.has_add_permission(None) is False
    assert admin.has_change_permission(None) is False
    assert admin.has_delete_permission(None) is False
    assert "cash_delta" in admin.readonly_fields
    assert "price_in" in admin.readonly_fields
