"""Private player watchlist behavior."""

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.fantasy import services
from apps.fantasy.models import WatchlistEntry
from apps.nba.models import Player

pytestmark = pytest.mark.django_db


@pytest.fixture
def signed_in(client, user, password):
    client.login(email=user.email, password=password)
    return client


@pytest.fixture
def player(db):
    return Player.objects.create(
        first_name="Watched",
        last_name="Player",
        position=Player.Position.GUARD,
        current_salary=Decimal("1000000"),
        current_fp_per_game=Decimal("20"),
        current_total_fp=Decimal("100"),
        current_games_played=10,
    )


def player_url(player):
    return reverse("nba:player-detail", args=[player.slug])


def toggle_url(player):
    return reverse("nba:watchlist-toggle", args=[player.slug])


def test_user_can_add_and_remove_a_player(signed_in, user, player):
    response = signed_in.post(toggle_url(player))

    assert response.status_code == 200
    assert WatchlistEntry.objects.filter(user=user, player=player).exists()
    assert "Remove" in response.content.decode()

    response = signed_in.post(toggle_url(player))

    assert response.status_code == 200
    assert not WatchlistEntry.objects.filter(user=user, player=player).exists()
    assert "Add" in response.content.decode()


def test_duplicate_watchlist_entries_are_idempotent(user, player):
    first, created = services.add_to_watchlist(user, player)
    second, created_again = services.add_to_watchlist(user, player)

    assert created
    assert not created_again
    assert first == second
    assert WatchlistEntry.objects.filter(user=user, player=player).count() == 1


def test_watchlist_is_private_on_the_watchlist_page(
    client, user, other_manager, player, password
):
    other_player = Player.objects.create(
        first_name="Other",
        last_name="Player",
        position=Player.Position.FORWARD,
        current_salary=Decimal("2000000"),
        current_fp_per_game=Decimal("22"),
    )
    services.add_to_watchlist(user, player)
    services.add_to_watchlist(other_manager.user, other_player)
    client.login(email=user.email, password=password)

    body = client.get(reverse("nba:watchlist")).content.decode()

    assert player.full_name in body
    assert other_player.full_name not in body


def test_player_detail_shows_only_the_current_users_watch_state(
    signed_in, user, other_manager, player
):
    services.add_to_watchlist(other_manager.user, player)

    body = signed_in.get(player_url(player)).content.decode()

    assert "Add Watched Player to watchlist" in body
    assert not WatchlistEntry.objects.filter(user=user, player=player).exists()


def test_watchlist_filter_controls_table_and_scatter_data(signed_in, user, player):
    other_player = Player.objects.create(
        first_name="Unwatched",
        last_name="Player",
        position=Player.Position.CENTER,
        current_salary=Decimal("3000000"),
        current_fp_per_game=Decimal("25"),
        current_total_fp=Decimal("120"),
        current_games_played=12,
    )
    services.add_to_watchlist(user, player)

    response = signed_in.get(reverse("nba:player-list") + "?watchlist=1")
    body = response.content.decode()

    assert response.context["watchlist_only"] is True
    assert player.full_name in body
    assert other_player.full_name not in body
    assert 'fill="currentColor"' in body
    assert 'name="watchlist"' in body
    assert "checked" in body.split('name="watchlist"', 1)[1].split(">", 1)[0]


def test_watchlist_page_keeps_inactive_entries_visible(signed_in, user, player):
    player.is_active = False
    player.save(update_fields=["is_active"])
    services.add_to_watchlist(user, player)

    body = signed_in.get(reverse("nba:watchlist")).content.decode()

    assert player.full_name in body
    assert "Inactive" in body


def test_removing_from_filtered_watchlist_removes_the_row(signed_in, user, player):
    services.add_to_watchlist(user, player)

    response = signed_in.post(toggle_url(player) + "?remove_row=1")

    assert response.status_code == 200
    assert response.content == b""
    assert not WatchlistEntry.objects.filter(user=user, player=player).exists()
