"""The kick list: the watchlist's negative twin, stored on the same row."""

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.fantasy import services
from apps.fantasy.models import Roster, Season, WatchlistEntry
from apps.nba.models import Player

pytestmark = pytest.mark.django_db

WATCH = WatchlistEntry.Stance.WATCH
KICK = WatchlistEntry.Stance.KICK


@pytest.fixture
def signed_in(client, user, password):
    client.login(email=user.email, password=password)
    return client


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
    today = timezone.localdate()
    season = Season.objects.create(
        label="Soon",
        starts_on=today + dt.timedelta(days=13),
        ends_on=today + dt.timedelta(days=183),
        is_current=True,
    )
    return Roster.objects.create(manager=manager, season=season, name="R")


def build(client, roster, **params):
    return client.get(
        reverse("fantasy:roster-build", args=[roster.pk]), params, headers={"HX-Request": "true"}
    )


def names(players):
    return sorted(player.last_name for player in players)


# --- the stored mark -------------------------------------------------------------


def test_new_entries_are_stars_by_default(user, market):
    entry = WatchlistEntry.objects.create(user=user, player=market["Ant"])

    assert entry.stance == WATCH


def test_kicking_is_idempotent(user, market):
    services.add_to_kick_list(user, market["Ant"])
    services.add_to_kick_list(user, market["Ant"])

    assert list(WatchlistEntry.objects.values_list("stance", flat=True)) == [KICK]


def test_kicking_a_starred_player_replaces_the_star(user, market):
    services.add_to_watchlist(user, market["Ant"])
    services.add_to_kick_list(user, market["Ant"])

    assert list(WatchlistEntry.objects.values_list("stance", flat=True)) == [KICK]


def test_starring_a_kicked_player_replaces_the_kick_mark(user, market):
    services.add_to_kick_list(user, market["Ant"])
    services.add_to_watchlist(user, market["Ant"])

    assert list(WatchlistEntry.objects.values_list("stance", flat=True)) == [WATCH]


def test_removing_the_star_leaves_a_kick_mark_alone(user, market):
    services.add_to_kick_list(user, market["Ant"])
    services.remove_from_watchlist(user, market["Ant"])

    assert list(WatchlistEntry.objects.values_list("stance", flat=True)) == [KICK]


def test_removing_the_kick_mark_leaves_a_star_alone(user, market):
    services.add_to_watchlist(user, market["Ant"])
    services.remove_from_kick_list(user, market["Ant"])

    assert list(WatchlistEntry.objects.values_list("stance", flat=True)) == [WATCH]


# --- kicked players stay out of everything the watchlist shows -----------------------


def test_kicked_players_are_not_in_the_market_watchlist(signed_in, user, roster, market):
    services.add_to_watchlist(user, market["Ant"])
    services.add_to_kick_list(user, market["Bee"])

    response = build(signed_in, roster, watchlist="1")

    assert names(response.context["available"]) == ["Ant"]
    assert response.context["watchlist_count"] == 1


def test_only_kicked_players_count_as_an_empty_watchlist(signed_in, user, roster, market):
    services.add_to_kick_list(user, market["Bee"])

    response = build(signed_in, roster, watchlist="1")

    assert response.context["watchlist_empty"] is True


def test_kicked_players_are_not_starred_in_the_market(signed_in, user, roster, market):
    services.add_to_kick_list(user, market["Bee"])

    response = build(signed_in, roster)

    assert not next(p for p in response.context["available"] if p.last_name == "Bee").is_watched


def test_kicked_players_are_not_on_the_watchlist_page(signed_in, user, market):
    services.add_to_watchlist(user, market["Ant"])
    services.add_to_kick_list(user, market["Bee"])

    response = signed_in.get(reverse("nba:watchlist"))

    assert names(response.context["players"]) == ["Ant"]


def test_kicked_players_are_not_starred_on_their_page(signed_in, user, market):
    services.add_to_kick_list(user, market["Ant"])

    response = signed_in.get(reverse("nba:player-detail", args=[market["Ant"].slug]))

    assert response.context["is_watched"] is False


def test_the_star_toggle_turns_a_kick_mark_into_a_star(signed_in, user, market):
    services.add_to_kick_list(user, market["Ant"])

    signed_in.post(reverse("nba:watchlist-toggle", args=[market["Ant"].slug]))

    assert list(WatchlistEntry.objects.values_list("stance", flat=True)) == [WATCH]
