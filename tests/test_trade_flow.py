"""The trade screen: one player out, one in, one transaction.

`services.trade` is covered in test_fantasy_models.py -- these are the decisions
that sit on top of it: what the two sides are worth, what the roster is allowed
to do to itself, and what the modal does with a half-made choice.
"""

import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.fantasy import services
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
def signed_in(client, user, password, db):
    client.login(email=user.email, password=password)
    return client


@pytest.fixture
def pool(db):
    """Cheap enough that a full roster fits inside the cap several times over."""
    return {
        position: [
            Player.objects.create(
                first_name=f"P{i}",
                last_name=position,
                position=position,
                current_salary=Decimal("1000000"),
            )
            for i in range(8)
        ]
        for position in Player.Position.values
    }


@pytest.fixture
def roster(season, manager):
    return Roster.objects.create(manager=manager, season=season, name="Roster 1")


@pytest.fixture
def squad(roster, pool):
    """A roster that meets every minimum: five Guards, five Forwards, two Center."""
    for position, count in (("G", 5), ("F", 5), ("C", 2)):
        for player in pool[position][:count]:
            services.buy(roster, player, player.current_salary)
    roster.refresh_from_db()
    return roster


def trade_url(roster, **params):
    url = reverse("fantasy:roster-trade", args=[roster.pk])
    if params:
        url += "?" + "&".join(f"{key}={value}" for key, value in params.items())
    return url


# --- what the two sides are worth --------------------------------------------


def test_both_sides_are_priced_at_todays_salary(roster, pool):
    """The point of trading: a player who has gained value is worth the gain.

    A sale refunds what was paid -- see `amount_paid_for` -- and a trade must not,
    or every rise in price would be lost on the way out.
    """
    out_player, in_player = pool["G"][0], pool["F"][0]
    services.buy(roster, out_player, Decimal("3000000"))
    Player.objects.filter(pk=out_player.pk).update(current_salary=Decimal("5000000"))
    out_player.refresh_from_db()

    assert services.amount_paid_for(roster, out_player) == Decimal("3000000")

    preview = services.trade_preview(roster, out_player, in_player)
    assert preview["salary_out"] == Decimal("5000000")
    assert preview["salary_in"] == Decimal("1000000")


def test_a_dearer_incoming_player_costs_the_difference(roster, pool):
    out_player, in_player = pool["G"][0], pool["F"][0]
    services.buy(roster, out_player, out_player.current_salary)
    Player.objects.filter(pk=in_player.pk).update(current_salary=Decimal("4000000"))
    in_player.refresh_from_db()

    preview = services.trade_preview(roster, out_player, in_player)
    assert preview["delta"] == Decimal("-3000000")
    assert preview["cash_after"] == roster.cash - Decimal("3000000")
    assert preview["affordable"] is True
    assert preview["ready"] is True


def test_a_cheaper_incoming_player_frees_up_cash(roster, pool):
    out_player, in_player = pool["G"][0], pool["F"][0]
    services.buy(roster, out_player, Decimal("9000000"))
    Player.objects.filter(pk=out_player.pk).update(current_salary=Decimal("9000000"))
    out_player.refresh_from_db()

    preview = services.trade_preview(roster, out_player, in_player)
    assert preview["delta"] == Decimal("8000000")
    assert preview["cash_after"] == roster.cash + Decimal("8000000")


def test_the_budget_is_the_cash_plus_the_outgoing_player(roster, pool):
    """What the incoming column is marked against, and why it is not the balance."""
    out_player = pool["G"][0]
    services.buy(roster, out_player, STARTING_CASH - Decimal("2000000"))
    Player.objects.filter(pk=out_player.pk).update(current_salary=Decimal("40000000"))
    out_player.refresh_from_db()

    roster.refresh_from_db()
    assert roster.cash == Decimal("2000000")

    preview = services.trade_preview(roster, out_player, None)
    assert preview["budget"] == Decimal("42000000")
    assert preview["ready"] is False


def test_a_trade_beyond_the_budget_is_marked_unaffordable(roster, pool):
    out_player, in_player = pool["G"][0], pool["F"][0]
    services.buy(roster, out_player, STARTING_CASH)
    Player.objects.filter(pk=in_player.pk).update(current_salary=Decimal("59000000"))
    in_player.refresh_from_db()

    preview = services.trade_preview(roster, out_player, in_player)
    assert preview["cash_after"] < 0
    assert preview["affordable"] is False


def test_half_a_choice_still_produces_figures(roster, pool):
    """The modal renders before either side is picked, so nothing may blow up."""
    preview = services.trade_preview(roster, None, None)
    assert preview["ready"] is False
    assert preview["delta"] == 0
    assert preview["cash_after"] == roster.cash
    assert preview["budget"] == roster.cash
    assert preview["breaks"] == []


# --- what it would do to the minimums ----------------------------------------


def test_a_trade_that_breaks_a_minimum_is_flagged(squad, pool):
    """A Center out and a Guard in leaves one Center, and the minimum is two."""
    preview = services.trade_preview(squad, pool["C"][0], pool["G"][5])
    assert preview["breaks"] == ["1 more Center"]
    # Flagged, not blocked -- the roster may sit incomplete between two moves.
    assert preview["affordable"] is True


def test_a_trade_within_one_position_is_not_flagged(squad, pool):
    preview = services.trade_preview(squad, pool["G"][0], pool["G"][5])
    assert preview["breaks"] == []


def test_a_shortfall_that_was_already_there_is_not_blamed_on_the_trade(roster, pool):
    """Only what this trade would newly break.

    A roster that is short a Center does not need telling again by a trade that
    never touched its Centers.
    """
    services.buy(roster, pool["G"][0], pool["G"][0].current_salary)
    assert "2 more Centers" in roster.missing()

    preview = services.trade_preview(roster, pool["G"][0], pool["G"][5])
    assert preview["breaks"] == []


def test_a_trade_that_deepens_a_shortfall_is_flagged(squad, pool):
    """One Center out, one Guard in, twice over: the second trade is worse."""
    services.trade(squad, pool["C"][0], pool["G"][5], price_out=1_000_000, price_in=1_000_000)
    preview = services.trade_preview(squad, pool["C"][1], pool["G"][6])
    assert preview["breaks"] == ["2 more Centers"]


# --- the modal ---------------------------------------------------------------


def test_the_build_page_offers_a_trade_button(signed_in, roster):
    body = signed_in.get(reverse("fantasy:roster-build", args=[roster.pk])).content.decode()
    assert trade_url(roster) in body
    # By the button's accessible name, which it carries at every width: below
    # `sm` the header shows the trade arrows alone and the word is display:none,
    # so pinning the exact indentation of the text asserted a layout rather
    # than a button.
    assert 'aria-label="Trade"' in body


def test_the_modal_lists_both_sides(signed_in, squad, pool):
    body = signed_in.get(trade_url(squad)).content.decode()
    assert "Player out" in body
    assert "Player in" in body
    # A player on the roster can only leave; one who is not can only arrive.
    assert f"?body=1&amp;out={pool['G'][0].pk}" in body
    assert f"?body=1&amp;in={pool['G'][5].pk}" in body
    assert f"in={pool['G'][0].pk}" not in body


def test_the_urls_in_the_markup_are_escaped(signed_in, squad):
    """Every `&` in an attribute, whether it came from the template or a variable.

    A bare `&` is an ambiguous ampersand; HTMX would still read the attribute
    back correctly, but the markup would not be valid and the two halves of the
    same URL would be written two different ways.
    """
    body = signed_in.get(trade_url(squad)).content.decode()
    assert "?body=1&amp;out=" in body
    assert "?body=1&out=" not in body


def test_the_modal_can_open_with_the_outgoing_player_chosen(signed_in, squad, pool):
    """The shortcut on each squad row, which is the common way in."""
    chosen = pool["C"][0]
    body = signed_in.get(trade_url(squad, out=chosen.pk)).content.decode()
    assert chosen.full_name in body
    # Every incoming option now carries the choice along, so picking one keeps it.
    # `&amp;` because these fragments are rendered into an attribute; HTMX reads
    # the parsed value back, so what it requests is a plain `&`.
    assert f"in={pool['G'][5].pk}&amp;out={chosen.pk}" in body
    assert "not chosen" in body  # the other side, not yet picked


def test_the_pool_is_marked_against_the_budget_not_the_balance(signed_in, roster, pool):
    """A player far beyond the balance is within reach once someone is going out."""
    out_player, in_player = pool["G"][0], pool["F"][0]
    services.buy(roster, out_player, STARTING_CASH - Decimal("1000000"))
    Player.objects.filter(pk=out_player.pk).update(current_salary=Decimal("50000000"))
    Player.objects.filter(pk=in_player.pk).update(current_salary=Decimal("40000000"))

    without = signed_in.get(trade_url(roster)).content.decode()
    with_out = signed_in.get(trade_url(roster, out=out_player.pk)).content.decode()

    # 40m is out of reach on a 1m balance, and comfortable on a 51m budget.
    assert "Budget $1.00M" in without
    assert "Budget $51.00M" in with_out
    assert "Too dear" not in with_out


def test_a_stale_outgoing_player_is_simply_not_chosen(signed_in, squad, pool):
    """A player sold in another tab leaves the screen rather than breaking it."""
    body = signed_in.get(trade_url(squad, out=pool["G"][5].pk)).content.decode()
    assert body.count("not chosen") == 2


def test_an_incoming_player_already_on_the_roster_is_not_chosen(signed_in, squad, pool):
    body = signed_in.get(trade_url(squad, **{"in": pool["G"][0].pk})).content.decode()
    assert body.count("not chosen") == 2


def test_a_malformed_id_is_not_an_error(signed_in, squad):
    response = signed_in.get(trade_url(squad, out="nicht-eine-uuid"))
    assert response.status_code == 200
    assert "not chosen" in response.content.decode()


def test_the_body_request_leaves_the_modal_shell_alone(signed_in, squad):
    """Selections swap the body only, so the modal does not reopen on every click."""
    full = signed_in.get(trade_url(squad)).content.decode()
    body = signed_in.get(trade_url(squad, body=1)).content.decode()

    assert 'id="trade-body"' in full
    assert 'id="trade-body"' in body
    assert 'role="dialog"' in full
    assert 'role="dialog"' not in body


def test_another_users_roster_is_not_found(signed_in, season, roster, other_manager):
    theirs = Roster.objects.create(manager=other_manager, season=season, name="Theirs")
    assert signed_in.get(trade_url(theirs)).status_code == 404


def test_the_modal_says_so_when_there_is_nobody_to_trade(signed_in, roster, pool):
    body = signed_in.get(trade_url(roster)).content.decode()
    assert "Nobody on the roster yet" in body


# --- carrying it out ---------------------------------------------------------


def test_the_trade_goes_through(signed_in, squad, pool):
    out_player, in_player = pool["C"][0], pool["F"][5]
    Player.objects.filter(pk=out_player.pk).update(current_salary=Decimal("4000000"))
    before = squad.cash

    response = signed_in.post(trade_url(squad, out=out_player.pk, **{"in": in_player.pk}))
    squad.refresh_from_db()

    assert response.status_code == 200
    assert response["HX-Trigger"] == "close-modal"
    assert out_player not in squad.current_players
    assert in_player in squad.current_players
    # 4m out, 1m in: the roster keeps the 3m difference.
    assert squad.cash == before + Decimal("3000000")

    txn = Transaction.objects.latest("occurred_at")
    assert txn.kind == Transaction.Kind.TRADE
    assert txn.cash_delta == Decimal("3000000")


def test_the_response_brings_back_the_panel_and_the_picker(signed_in, squad, pool):
    body = signed_in.post(
        trade_url(squad, out=pool["C"][0].pk, **{"in": pool["F"][5].pk})
    ).content.decode()
    assert 'id="roster-panel"' in body
    assert 'id="player-picker"' in body
    assert 'hx-swap-oob="outerHTML"' in body


def test_the_prices_come_from_the_players_not_the_request(signed_in, squad, pool):
    """A client that could name both prices could trade anyone for anyone."""
    out_player, in_player = pool["C"][0], pool["F"][5]
    Player.objects.filter(pk=in_player.pk).update(current_salary=Decimal("7000000"))
    before = squad.cash

    signed_in.post(
        trade_url(squad, out=out_player.pk, **{"in": in_player.pk}),
        {"price_out": "50000000", "price_in": "0"},
    )
    squad.refresh_from_db()

    # 1m out, 7m in -- the posted prices are ignored.
    assert squad.cash == before - Decimal("6000000")


def test_a_trade_that_cannot_be_afforded_keeps_the_modal_open(signed_in, roster, pool):
    out_player, in_player = pool["G"][0], pool["F"][0]
    services.buy(roster, out_player, STARTING_CASH)
    Player.objects.filter(pk=in_player.pk).update(current_salary=Decimal("59000000"))
    roster.refresh_from_db()

    response = signed_in.post(trade_url(roster, out=out_player.pk, **{"in": in_player.pk}))
    roster.refresh_from_db()

    assert response.status_code == 200
    # The confirm button aims at the roster panel; an error is sent back to the
    # modal instead, with both choices still made.
    assert response["HX-Retarget"] == "#trade-body"
    assert "Not enough cash" in response.content.decode()
    assert out_player in roster.current_players
    assert in_player not in roster.current_players
    assert not Transaction.objects.filter(player_out=out_player).exists()


def test_a_trade_with_only_one_side_chosen_is_refused(signed_in, squad, pool):
    response = signed_in.post(trade_url(squad, out=pool["C"][0].pk))
    assert response["HX-Retarget"] == "#trade-body"
    assert "Pick a player to send out" in response.content.decode()
    assert pool["C"][0] in squad.current_players


def test_a_trade_that_breaks_a_minimum_still_goes_through(signed_in, squad, pool):
    """Allowed on purpose: the roster may sit incomplete between two moves."""
    signed_in.post(trade_url(squad, out=pool["C"][0].pk, **{"in": pool["G"][5].pk}))
    squad.refresh_from_db()

    assert pool["G"][5] in squad.current_players
    assert squad.position_counts()["C"] == 1
    assert "1 more Center" in squad.missing()
