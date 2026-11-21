"""The screens for correcting a past move: the history page's actions and modals.

The rules are `test_move_correction_ledger`'s and the writes
`test_move_correction_services`'; these are about what a manager sees and may
reach -- only their own correctable moves, a preview that says no before the
button does, and a refusal that still holds when the button is bypassed.
"""

import datetime as dt
import re

import pytest
from django.urls import reverse

from apps.fantasy.models import Roster, RosterPlayer, Transaction, TransactionCorrection

from .move_correction_factories import World, at

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(manager):
    return World(manager)


@pytest.fixture
def signed_in(client, user, password):
    client.login(email=user.email, password=password)
    return client


def date_url(world, move):
    return reverse("fantasy:roster-move-date", args=[world.roster.pk, move.pk])


def undo_url(world, move):
    return reverse("fantasy:roster-move-undo", args=[world.roster.pk, move.pk])


def history_url(world):
    return reverse("fantasy:roster-history", args=[world.roster.pk])


@pytest.fixture
def traded(world):
    a, b = world.player(5, name="Brunson"), world.player(3, name="Ball")
    world.sign(a)
    return world.trade(a, b, world.week1(2))


# --- the history page -----------------------------------------------------------


def test_the_history_offers_corrections_on_trades_only(signed_in, world, traded):
    page = signed_in.get(history_url(world))

    assert page.status_code == 200
    html = page.content.decode()
    assert date_url(world, traded) in html
    assert undo_url(world, traded) in html
    signing = world.roster.transactions.get(kind=Transaction.Kind.BUY)
    assert str(signing.pk) not in html
    rows = {row["move"].kind: row["correctable"] for row in page.context["rows"]}
    assert rows == {"TRADE": True, "BUY": False, "GRANT_TRADE": False}
    assert page.context["any_correctable"]


def test_a_history_without_trades_shows_no_correction_hint(signed_in, world):
    world.sign(world.player(2))

    page = signed_in.get(history_url(world))
    assert not page.context["any_correctable"]
    assert "Change its date or undo it" not in page.content.decode()


def test_a_moved_trade_says_so_on_the_history(signed_in, world, traded):
    signed_in.post(date_url(world, traded), {"day": world.week1(4).isoformat()})

    page = signed_in.get(history_url(world))
    row = next(r for r in page.context["rows"] if r["move"].pk == traded.pk)
    assert row["moved_from"] == at(world.week1(2))
    assert "Date changed" in page.content.decode()
    assert page.context["reconciles"]


def test_the_corrections_cost_the_history_page_one_query(
    signed_in, world, traded, django_assert_max_num_queries
):
    """One query for the moved-from dates, however many rows there are."""
    signed_in.get(history_url(world))  # warm up: grants paid, sessions touched
    with django_assert_max_num_queries(30) as captured:
        signed_in.get(history_url(world))
    corrections = [q for q in captured.captured_queries if "transactioncorrection" in q["sql"]]
    assert len(corrections) == 1


# --- the change-date modal ------------------------------------------------------


def test_the_date_modal_opens_with_the_move_and_no_verdict_yet(signed_in, world, traded):
    page = signed_in.get(date_url(world, traded))

    assert page.status_code == 200
    html = page.content.decode()
    assert "Change the date" in html
    assert "Brunson" in html and "Ball" in html
    assert 'type="date"' in html
    assert f'min="{world.season.starts_on.isoformat()}"' in html
    assert "Pick a day to see what changes." in html
    assert page.context["correction"] is None


def test_the_preview_follows_the_picked_day(signed_in, world, traded):
    page = signed_in.get(date_url(world, traded), {"day": world.week1(4).isoformat(), "body": 1})

    assert page.status_code == 200
    html = page.content.decode()
    assert 'id="move-preview"' in html
    assert "Every move after it still works out." in html
    submit = re.search(r'<button type="submit"[^>]*>', html).group(0)
    assert "disabled" not in submit
    assert "<form" not in html  # only the preview, swapped into the open modal
    assert page.context["correction"].ok


def test_a_preview_with_a_problem_disables_the_button(signed_in, world):
    a, b, c = world.player(2), world.player(2), world.player(2)
    world.sign(a)
    first = world.trade(a, b, world.week1(1))
    world.trade(b, c, world.week1(3))

    page = signed_in.get(date_url(world, first), {"day": world.week1(5).isoformat(), "body": 1})

    html = page.content.decode()
    assert "This would not work out" in html
    assert "would leave the roster before joining it" in html
    submit = re.search(r'<button type="submit"[^>]*>', html).group(0)
    assert "disabled" in submit


def test_posting_a_day_moves_the_trade_and_redirects(signed_in, world, traded, user):
    response = signed_in.post(date_url(world, traded), {"day": world.week1(4).isoformat()})

    assert response.status_code == 204
    assert response["HX-Redirect"] == history_url(world)
    traded.refresh_from_db()
    assert traded.occurred_at == at(world.week1(4))
    log = TransactionCorrection.objects.get()
    assert log.made_by == user


def test_a_refused_post_reopens_the_modal_with_the_reason(signed_in, world):
    """The preview is a courtesy; the service is the gate."""
    a, b, c = world.player(2), world.player(2), world.player(2)
    world.sign(a)
    first = world.trade(a, b, world.week1(1))
    world.trade(b, c, world.week1(3))

    response = signed_in.post(date_url(world, first), {"day": world.week1(5).isoformat()})

    assert response.status_code == 200
    assert "would leave the roster before joining it" in response.content.decode()
    first.refresh_from_db()
    assert first.occurred_at == at(world.week1(1))


def test_a_post_without_a_day_asks_for_one(signed_in, world, traded):
    response = signed_in.post(date_url(world, traded), {"day": "not-a-date"})

    assert response.status_code == 200
    assert "Pick a day." in response.content.decode()


def test_the_success_message_lands_on_the_history(signed_in, world, traded):
    signed_in.post(date_url(world, traded), {"day": world.week1(4).isoformat()})

    page = signed_in.get(history_url(world))
    assert "Trade Brunson → Ball moved to" in page.content.decode()


# --- the undo modal ---------------------------------------------------------------


def test_the_undo_modal_says_what_happens(signed_in, world, traded):
    page = signed_in.get(undo_url(world, traded))

    html = page.content.decode()
    assert "Undo this trade?" in html
    assert "comes back to the roster" in html
    assert "Undo trade" in html


def test_an_undo_that_cannot_happen_offers_no_button(signed_in, world):
    a, b, c = world.player(2), world.player(2), world.player(2)
    world.sign(a)
    first = world.trade(a, b, world.week1(1))
    world.trade(b, c, world.week1(3))

    html = signed_in.get(undo_url(world, first)).content.decode()

    assert "Undo that move first" in html
    assert "btn btn-danger" not in html
    assert "Close" in html


def test_posting_an_undo_removes_the_trade(signed_in, world, traded):
    response = signed_in.post(undo_url(world, traded))

    assert response.status_code == 204
    assert response["HX-Redirect"] == history_url(world)
    assert not Transaction.objects.filter(pk=traded.pk).exists()
    page = signed_in.get(history_url(world))
    assert page.context["reconciles"]
    assert "Trade Brunson → Ball undone." in page.content.decode()


def test_a_refused_undo_post_keeps_the_trade(signed_in, world):
    a, b, c = world.player(2), world.player(2), world.player(2)
    world.sign(a)
    first = world.trade(a, b, world.week1(1))
    world.trade(b, c, world.week1(3))

    response = signed_in.post(undo_url(world, first))

    assert response.status_code == 200
    assert Transaction.objects.filter(pk=first.pk).exists()


def test_undoing_a_bought_trade_is_offered_too(signed_in, world):
    bought = world.buy_trade(world.week1(2))

    html = signed_in.get(undo_url(world, bought)).content.decode()
    assert "Undo this trade purchase?" in html
    assert "Undo purchase" in html


# --- who may reach what -----------------------------------------------------------


def test_someone_elses_roster_is_a_404(client, world, traded, other_manager):
    client.force_login(other_manager.user)

    assert client.get(date_url(world, traded)).status_code == 404
    assert client.post(undo_url(world, traded)).status_code == 404
    assert Transaction.objects.filter(pk=traded.pk).exists()


def test_a_move_of_another_roster_is_a_404_under_your_own(signed_in, world, other_manager):
    theirs = Roster.objects.create(manager=other_manager, season=world.season, name="Rivals")
    a, b = world.player(2), world.player(2)
    RosterPlayer.objects.create(roster=theirs, player=a, added_at=at(world.season.starts_on))
    their_trade = Transaction.objects.create(
        roster=theirs,
        occurred_at=at(world.week1(2)),
        player_out=a,
        player_in=b,
        price_out=a.current_salary,
        price_in=b.current_salary,
        cash_delta=0,
    )

    # Your roster in the URL, their move: not found, never touched.
    url = reverse("fantasy:roster-move-undo", args=[world.roster.pk, their_trade.pk])
    assert signed_in.post(url).status_code == 404


def test_signings_and_free_trades_are_a_404(signed_in, world, traded):
    signing = world.roster.transactions.get(kind=Transaction.Kind.BUY)
    grant = world.roster.transactions.get(kind=Transaction.Kind.GRANT_TRADE)

    for move in (signing, grant):
        assert signed_in.get(date_url(world, move)).status_code == 404
        assert signed_in.post(undo_url(world, move)).status_code == 404


def test_signed_out_visitors_are_sent_to_sign_in(client, world, traded):
    response = client.get(date_url(world, traded))
    assert response.status_code == 302


def test_after_the_season_the_modal_explains_instead(signed_in, world, traded):
    world.season.ends_on = world.week1(5)
    world.season.starts_on = world.season.ends_on - dt.timedelta(days=60)
    world.season.save()

    page = signed_in.get(history_url(world))
    assert not page.context["any_correctable"]
    html = signed_in.get(undo_url(world, traded)).content.decode()
    assert "can no longer be changed" in html
