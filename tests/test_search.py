"""The search boxes: one letter is not a search, two letters are."""

import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.http import QueryDict
from django.urls import reverse
from django.utils import timezone

from apps.core.search import SEARCH_MIN_LENGTH, search_query
from apps.fantasy import services
from apps.fantasy.models import Roster, Season
from apps.nba.models import Player

User = get_user_model()


# --- the rule ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", ""),
        ("   ", ""),
        ("a", ""),
        (" a ", ""),
        ("ab", "ab"),
        ("  le  ", "le"),
        ("james l", "james l"),
    ],
)
def test_the_query_needs_two_letters(raw, expected):
    assert search_query(QueryDict(mutable=True) | {"q": raw}) == expected


def test_a_missing_query_is_no_search():
    assert search_query(QueryDict()) == ""


def test_the_minimum_is_two():
    # The templates' `hx-trigger` filters skip exactly one letter; they and
    # this rule have to agree.
    assert SEARCH_MIN_LENGTH == 2


# --- the pages -----------------------------------------------------------------


@pytest.fixture
def signed_in(client, user, password):
    client.login(email=user.email, password=password)
    return client


def make_player(last_name, position="G"):
    return Player.objects.create(
        first_name="P", last_name=last_name, position=position, current_salary=1_000_000
    )


@pytest.fixture
def market(db):
    return {name: make_player(name) for name in ["Ant", "Bee", "Cat", "Dog"]}


def make_season(started):
    today = timezone.localdate()
    start = today - dt.timedelta(days=30) if started else today + dt.timedelta(days=13)
    return Season.objects.create(
        label="Live" if started else "Soon",
        starts_on=start,
        ends_on=start + dt.timedelta(days=170),
        is_current=True,
    )


def player_list_names(response):
    return sorted(player.last_name for player in response.context["players"])


def available_names(response):
    return sorted(player.last_name for player in response.context["available"])


def test_the_players_page_ignores_one_letter(signed_in, market):
    url = reverse("nba:player-list")

    assert player_list_names(signed_in.get(url, {"q": "a"})) == ["Ant", "Bee", "Cat", "Dog"]
    assert player_list_names(signed_in.get(url, {"q": "an"})) == ["Ant"]


def test_the_excel_export_does_not_name_a_one_letter_search(signed_in, market):
    from apps.nba.player_export import describe_filters

    request = signed_in.get(reverse("nba:player-list"), {"q": "a"}).wsgi_request
    phrases = describe_filters(
        request,
        watchlist_page=False,
        kicklist_only=False,
        last_season_short=False,
        min_games=None,
        next_days=None,
    )

    assert not any("Search" in phrase for phrase in phrases)


def test_the_roster_market_ignores_one_letter(signed_in, manager, market):
    roster = Roster.objects.create(manager=manager, season=make_season(started=False), name="R")
    url = reverse("fantasy:roster-build", args=[roster.pk])

    def build(q):
        return signed_in.get(url, {"q": q}, headers={"HX-Request": "true"})

    assert available_names(build("a")) == ["Ant", "Bee", "Cat", "Dog"]
    assert available_names(build("an")) == ["Ant"]


def test_the_trade_dialog_ignores_one_letter(signed_in, manager, market):
    roster = Roster.objects.create(
        manager=manager, season=make_season(started=True), name="T", trades_available=1
    )
    Season.objects.filter(pk=roster.season_id).update(signings_close_at=None)
    out = make_player("Out")
    services.buy(roster, out, Decimal("1000000"))
    url = reverse("fantasy:roster-trade", args=[roster.pk])

    def trade(q):
        return signed_in.get(url, {"body": "1", "out": out.pk, "q": q})

    assert available_names(trade("a")) == ["Ant", "Bee", "Cat", "Dog"]
    assert available_names(trade("an")) == ["Ant"]


def test_the_compare_dialog_ignores_one_letter(signed_in, market):
    url = reverse("nba:player-compare-modal")

    one = signed_in.get(url, {"q": "a", "body": "1"}).text
    two = signed_in.get(url, {"q": "an", "body": "1"}).text

    assert all(f"P {name}" in one for name in ["Ant", "Bee", "Cat", "Dog"])
    assert "P Ant" in two
    assert "P Bee" not in two


def test_the_user_list_ignores_one_letter(client, staff_user, password, user):
    client.login(email=staff_user.email, password=password)
    url = reverse("accounts:user-list")

    def emails(q):
        response = client.get(url, {"q": q}, headers={"hx-request": "true"})
        return {account.email for account in response.context["users"]}

    # "h" is only in coach@…, so a one-letter search would drop team@….
    assert emails("h") == set(User.objects.values_list("email", flat=True))
    assert emails(user.email[:4]) == {user.email}
