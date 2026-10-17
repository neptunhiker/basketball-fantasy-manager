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


# --- the toggles -------------------------------------------------------------------


def kick_url(player, marks=None):
    url = reverse("nba:kick-toggle", args=[player.slug])
    return f"{url}?marks={marks}" if marks else url


def test_the_kick_toggle_needs_a_login(client, market):
    response = client.post(kick_url(market["Ant"]))

    assert response.status_code == 302
    assert not WatchlistEntry.objects.exists()


def test_the_kick_toggle_sets_and_lifts_the_mark(signed_in, user, market):
    on = signed_in.post(kick_url(market["Ant"], "kick"))

    assert WatchlistEntry.objects.get(user=user).stance == KICK
    assert "Remove P Ant from kick list" in on.text
    assert "data-kicked" in on.text
    assert on["HX-Trigger"] == "kicklist-changed"

    off = signed_in.post(kick_url(market["Ant"], "kick"))

    assert not WatchlistEntry.objects.exists()
    assert "Add P Ant to kick list" in off.text
    assert "data-kicked" not in off.text


def test_kicking_a_starred_player_redraws_both_marks(signed_in, user, market):
    services.add_to_watchlist(user, market["Ant"])

    response = signed_in.post(kick_url(market["Ant"], "both"))

    assert "Add P Ant to watchlist" in response.text
    assert "Remove P Ant from kick list" in response.text
    # The market counts stars, so it refreshes when one was lifted.
    assert response["HX-Trigger"] == "kicklist-changed, watchlist-changed"


def test_starring_a_kicked_player_redraws_both_marks(signed_in, user, market):
    services.add_to_kick_list(user, market["Ant"])

    response = signed_in.post(
        reverse("nba:watchlist-toggle", args=[market["Ant"].slug]) + "?marks=both"
    )

    assert "Remove P Ant from watchlist" in response.text
    assert "Add P Ant to kick list" in response.text
    assert response["HX-Trigger"] == "watchlist-changed, kicklist-changed"


def test_the_kick_toggle_answers_in_the_shape_it_was_asked_for(signed_in, user, market):
    kick_only = signed_in.post(kick_url(market["Ant"], "kick")).text
    signed_in.post(kick_url(market["Ant"]))
    both = signed_in.post(kick_url(market["Ant"], "both")).text

    assert "to watchlist" not in kick_only
    assert "to watchlist" in both


def test_in_a_list_a_kicked_player_shows_the_kick_mark_in_place_of_the_star(
    signed_in, user, market
):
    services.add_to_kick_list(user, market["Ant"])

    # Lifting the kick from a list brings the star back.
    response = signed_in.post(kick_url(market["Ant"], "star"))

    assert "Add P Ant to watchlist" in response.text
    assert "kick list" not in response.text


def test_an_unknown_shape_falls_back_to_the_list_slot(signed_in, market):
    response = signed_in.post(kick_url(market["Ant"], "nonsense"))

    # Kicked, in the list slot: the kick button replaces the star.
    assert "Remove P Ant from kick list" in response.text
    assert "to watchlist" not in response.text


# --- where the kick button lives -------------------------------------------------------


def test_the_player_page_offers_star_and_kick(signed_in, user, market):
    services.add_to_kick_list(user, market["Ant"])

    response = signed_in.get(reverse("nba:player-detail", args=[market["Ant"].slug]))

    assert response.context["is_kicked"] is True
    assert "Add P Ant to watchlist" in response.text
    assert "Remove P Ant from kick list" in response.text


def test_the_squad_offers_a_kick_button_per_player(signed_in, user, roster, market):
    services.buy(roster, market["Ant"], market["Ant"].current_salary)
    services.buy(roster, market["Bee"], market["Bee"].current_salary)
    services.add_to_kick_list(user, market["Ant"])

    response = signed_in.get(reverse("fantasy:roster-build", args=[roster.pk]))

    kicked = {m.player.last_name: m.player.is_kicked for m in response.context["memberships"]}
    assert kicked == {"Ant": True, "Bee": False}
    # Table and phone cards both carry the toggle.
    assert response.text.count("Remove P Ant from kick list") == 2
    assert response.text.count("Add P Bee to kick list") == 2
    assert "Kick candidate" in response.text


def test_another_accounts_kick_marks_stay_private(signed_in, user, roster, market, password):
    from django.contrib.auth import get_user_model

    other = get_user_model().objects.create_user(email="other@example.com", password=password)
    services.buy(roster, market["Ant"], market["Ant"].current_salary)
    services.add_to_kick_list(other, market["Ant"])

    response = signed_in.get(reverse("fantasy:roster-build", args=[roster.pk]))

    assert not response.context["memberships"][0].player.is_kicked
