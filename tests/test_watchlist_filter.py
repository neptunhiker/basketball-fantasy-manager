"""The watchlist filter on the roster page's market and in the trade dialog."""

import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.fantasy import services
from apps.fantasy.models import Roster, Season, WatchlistEntry
from apps.nba.models import Player

User = get_user_model()


@pytest.fixture
def signed_in(client, user, password):
    client.login(email=user.email, password=password)
    return client


def make_season(started):
    today = timezone.localdate()
    start = today - dt.timedelta(days=30) if started else today + dt.timedelta(days=13)
    return Season.objects.create(
        label="Live" if started else "Soon",
        starts_on=start,
        ends_on=start + dt.timedelta(days=170),
        is_current=True,
    )


def make_player(last_name, position="G", salary=1_000_000, **extra):
    return Player.objects.create(
        first_name="P", last_name=last_name, position=position, current_salary=salary, **extra
    )


@pytest.fixture
def market(db):
    return {
        name: make_player(name, position)
        for name, position in [("Ant", "G"), ("Bee", "F"), ("Cat", "C"), ("Dog", "G")]
    }


@pytest.fixture
def roster(manager, db):
    return Roster.objects.create(manager=manager, season=make_season(started=False), name="R")


def watch(user, *players):
    for player in players:
        WatchlistEntry.objects.create(user=user, player=player)


def build(client, roster, **params):
    return client.get(
        reverse("fantasy:roster-build", args=[roster.pk]), params, headers={"HX-Request": "true"}
    )


def names(response):
    return sorted(player.last_name for player in response.context["available"])


# --- the market ----------------------------------------------------------------


def test_the_filter_keeps_only_watched_players(signed_in, user, roster, market):
    watch(user, market["Ant"], market["Cat"])

    assert names(build(signed_in, roster)) == ["Ant", "Bee", "Cat", "Dog"]
    assert names(build(signed_in, roster, watchlist="1")) == ["Ant", "Cat"]


def test_another_accounts_watchlist_does_not_count(signed_in, user, roster, market, password):
    other = User.objects.create_user(email="other@example.com", password=password)
    watch(other, market["Bee"])
    watch(user, market["Ant"])

    response = build(signed_in, roster, watchlist="1")

    assert names(response) == ["Ant"]
    assert response.context["watchlist_count"] == 1


def test_the_filter_combines_with_search_position_and_team(signed_in, user, roster, market):
    watch(user, market["Ant"], market["Dog"], market["Cat"])

    assert names(build(signed_in, roster, watchlist="1", position="G")) == ["Ant", "Dog"]
    assert names(build(signed_in, roster, watchlist="1", q="do")) == ["Dog"]


def test_the_count_leaves_out_signed_inactive_and_unpriced_players(signed_in, user, roster, market):
    retired = make_player("Old", is_active=False)
    unpriced = make_player("New", salary=None)
    watch(user, market["Ant"], market["Bee"], retired, unpriced)
    services.buy(roster, market["Ant"], Decimal("1000000"))

    response = build(signed_in, roster, q="nobody")

    # Only Bee is still out there; the search does not change the count.
    assert response.context["watchlist_count"] == 1
    assert "Watchlist (1)" in response.text


def test_the_chip_is_a_checkbox_in_the_filter_form(signed_in, user, roster, market):
    off = build(signed_in, roster).text
    on = build(signed_in, roster, watchlist="1").text

    form = off[
        off.index('id="picker-filters"') : off.index("</form>", off.index('id="picker-filters"'))
    ]
    assert 'name="watchlist" value="1"' in form
    assert "change from:input[type=checkbox]" in off
    assert 'name="watchlist" value="1" checked' in on
    assert 'name="watchlist" value="1" checked' not in off


def test_signing_a_player_keeps_the_filter(signed_in, user, roster, market):
    watch(user, market["Ant"], market["Bee"])

    response = signed_in.post(
        reverse("fantasy:roster-buy", args=[roster.pk, market["Ant"].pk]),
        {"watchlist": "1"},
        headers={"HX-Request": "true"},
    )

    assert names(response) == ["Bee"]
    assert response.context["watchlist_count"] == 1


def test_every_row_has_a_star_toggle(signed_in, user, roster, market):
    watch(user, market["Ant"])

    body = build(signed_in, roster).text

    for player in market.values():
        assert f'hx-post="{reverse("nba:watchlist-toggle", args=[player.slug])}"' in body
    assert "Remove P Ant from watchlist" in body
    assert "Add P Bee to watchlist" in body
    assert "watchlist-changed from:body" in body


def test_toggling_the_star_tells_the_page_to_refresh(signed_in, market):
    response = signed_in.post(reverse("nba:watchlist-toggle", args=[market["Ant"].slug]))

    assert response["HX-Trigger"] == "watchlist-changed"


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        ("empty", "Your watchlist is empty."),
        ("all_signed", "None of your watched players can be added"),
        ("filtered", "None of your watched players match those filters."),
    ],
)
def test_the_empty_list_says_why(signed_in, user, roster, market, setup, message):
    if setup == "all_signed":
        watch(user, market["Ant"])
        services.buy(roster, market["Ant"], Decimal("1000000"))
    elif setup == "filtered":
        watch(user, market["Ant"])

    body = build(signed_in, roster, watchlist="1", position="C").text

    assert message in body


# --- the trade dialog ----------------------------------------------------------


@pytest.fixture
def trade_roster(manager, market):
    roster = Roster.objects.create(
        manager=manager, season=make_season(started=True), name="T", trades_available=1
    )
    Season.objects.filter(pk=roster.season_id).update(signings_close_at=None)
    out = make_player("Out", "G")
    services.buy(roster, out, Decimal("1000000"))
    return roster, out


def trade(client, roster, **params):
    return client.get(reverse("fantasy:roster-trade", args=[roster.pk]), {"body": "1", **params})


def test_the_trade_dialog_filters_incoming_players(signed_in, user, trade_roster, market):
    roster, out = trade_roster
    watch(user, market["Bee"])

    response = trade(signed_in, roster, out=out.pk, watchlist="1")

    assert names(response) == ["Bee"]
    assert 'name="watchlist" value="1" checked' in response.text
    assert "Watchlist (1)" in response.text


def test_the_trade_dialog_marks_watched_players(signed_in, user, trade_roster, market):
    roster, out = trade_roster
    watch(user, market["Bee"])

    body = trade(signed_in, roster, out=out.pk).text

    assert body.count('aria-label="On your watchlist"') == 1


def test_every_link_in_the_dialog_sends_the_filter_along(signed_in, user, trade_roster):
    """Picking a side re-renders the dialog; the chip must survive it."""
    roster, out = trade_roster

    body = trade(signed_in, roster, out=out.pk).text

    # The rows, the step-back button and the form itself all carry the form.
    for marker in ["?body=1&amp;out=", "?body=1&amp;in=", "Change player out"]:
        start = body.index(marker)
        tag = body[body.rindex("<button", 0, start) : body.index(">", start)]
        assert 'hx-include="#trade-filters"' in tag, marker
