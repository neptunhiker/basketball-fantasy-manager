"""Latest FP: what a player scored between his two newest snapshots."""

import datetime as dt
from decimal import Decimal

import pytest
from django.db.models import Prefetch
from django.urls import reverse
from django.utils import timezone

from apps.fantasy import services
from apps.fantasy.models import PlayerSnapshot, Roster, Season
from apps.nba.models import Player, Team
from apps.nba.views import with_latest_fp_change

pytestmark = pytest.mark.django_db


@pytest.fixture
def season(db):
    today = timezone.localdate()
    return Season.objects.create(
        label="2026-27",
        starts_on=today - dt.timedelta(days=30),
        ends_on=today + dt.timedelta(days=150),
        is_current=True,
    )


@pytest.fixture
def signed_in(client, user, password):
    client.login(email=user.email, password=password)
    return client


def make_player(last_name, position="G"):
    return Player.objects.create(
        first_name="P",
        last_name=last_name,
        position=position,
        team=Team.objects.get(abbreviation="LAL"),
    )


def record_days(player, season, days, start_days_ago=10):
    """One snapshot per day, oldest first, each a `(total_fp, games_played)` pair."""
    start = timezone.now() - dt.timedelta(days=start_days_ago)
    for index, (total_fp, games_played) in enumerate(days):
        services.record_snapshot(
            player,
            season,
            start + dt.timedelta(days=index),
            salary=1_000_000,
            total_fp=Decimal(str(total_fp)),
            games_played=games_played,
            team=player.team,
            position=player.position,
        )
    player.refresh_from_db()
    return player


def annotated(player):
    return with_latest_fp_change(Player.objects.filter(pk=player.pk)).get()


# --- the helper and its database twin ----------------------------------------


# (days, expected gain, expected change in the average)
CASES = {
    "a game": ([(100, 4), (148.5, 5)], Decimal("48.5"), Decimal("4.70")),
    "a rest day": ([(100, 4), (100, 4)], None, None),
    "played and scored nothing": ([(100, 4), (100, 5)], Decimal("0"), Decimal("-5.00")),
    "a negative night": ([(100, 4), (97, 5)], Decimal("-3"), Decimal("-5.60")),
    "two games between downloads": ([(100, 4), (160, 6)], Decimal("60"), Decimal("1.67")),
    "a corrected games count": ([(100, 5), (100, 4)], None, None),
    "the first game": ([(0, 0), (30, 1)], Decimal("30"), None),
    "a single snapshot": ([(100, 4)], None, None),
    "only the newest two count": ([(10, 1), (100, 4), (100, 4)], None, None),
}


@pytest.mark.parametrize("case", CASES)
def test_python_and_database_agree(case, season):
    days, gain, avg_change = CASES[case]
    player = record_days(make_player("Case"), season, days)

    change = player.latest_fp_change()
    row = annotated(player)

    assert (change or {}).get("fp") == gain
    assert row.latest_fp_gain == gain
    assert (change or {}).get("fp_per_game") == avg_change
    assert row.latest_avg_change == avg_change


def test_a_rest_day_still_reports_the_two_snapshots(season):
    player = record_days(make_player("Rest"), season, [(100, 4), (100, 4)])

    change = player.latest_fp_change()

    assert change["fp"] is None
    newest, previous = player.snapshots.order_by("-as_of")[:2]
    assert change["as_of"] == newest.as_of
    assert change["previous_as_of"] == previous.as_of


def test_a_new_season_is_not_compared_with_the_last(season):
    prior = Season.objects.create(
        label="2025-26", starts_on=dt.date(2025, 10, 1), ends_on=dt.date(2026, 6, 30)
    )
    player = make_player("Seasonal")
    record_days(player, prior, [(1500, 60)], start_days_ago=20)
    record_days(player, season, [(30, 1)], start_days_ago=1)

    assert player.latest_fp_change() is None
    assert annotated(player).latest_fp_gain is None


def test_prefetched_snapshots_cost_no_query(season, django_assert_num_queries):
    player = record_days(make_player("Cached"), season, [(100, 4), (120, 5)])
    prefetched = Player.objects.prefetch_related(
        Prefetch("snapshots", queryset=PlayerSnapshot.objects.order_by("-as_of"))
    ).get(pk=player.pk)

    with django_assert_num_queries(0):
        assert prefetched.latest_fp_change()["fp"] == Decimal("20")


# --- the player list ---------------------------------------------------------


@pytest.fixture
def scorers(season):
    return {
        "High": record_days(make_player("High"), season, [(100, 4), (160, 5)]),
        "Low": record_days(make_player("Low"), season, [(100, 4), (110, 5)]),
        "Zero": record_days(make_player("Zero"), season, [(100, 4), (100, 5)]),
        "Rested": record_days(make_player("Rested"), season, [(100, 4), (100, 4)]),
    }


def listed(response):
    return [player.last_name for player in response.context["players"]]


def test_the_player_list_sorts_by_latest_fp_with_rest_days_last(signed_in, scorers):
    url = reverse("nba:player-list")

    descending = signed_in.get(url, {"sort": "latest"})
    ascending = signed_in.get(url, {"sort": "latest", "dir": "asc"})

    # Descending first, like every number column; no game sinks in both.
    assert listed(descending) == ["High", "Low", "Zero", "Rested"]
    assert listed(ascending) == ["Zero", "Low", "High", "Rested"]


def test_the_player_list_tells_a_rest_day_from_a_scoreless_game(signed_in, scorers):
    body = signed_in.get(reverse("nba:player-list"), {"sort": "latest"}).text

    assert "Latest FP" in body
    assert "+60.0" in body
    assert ">0.0<" in body
    assert "No game since the previous snapshot" in body


def test_the_team_page_sorts_by_latest_fp(signed_in, scorers):
    response = signed_in.get(reverse("nba:team-detail", args=["lal"]), {"sort": "latest"})

    assert listed(response) == ["High", "Low", "Zero", "Rested"]
    assert "Latest FP" in response.text


def test_the_column_is_translated(signed_in, scorers, user):
    user.language = "de"
    user.save(update_fields=["language"])

    body = signed_in.get(reverse("nba:player-list")).text

    assert "Letzte FP" in body
    assert "Kein Spiel seit dem vorherigen Snapshot" in body


# --- the player page ---------------------------------------------------------


def detail(client, player):
    return client.get(reverse("nba:player-detail", args=[player.slug]))


def test_the_player_page_shows_the_latest_game(signed_in, scorers):
    response = detail(signed_in, scorers["High"])

    assert response.context["latest_change"]["fp"] == Decimal("60")
    assert "+60.0" in response.text
    assert "+7.00" in response.text  # 25.00 -> 32.00 per game


def test_the_player_page_says_when_he_last_did_not_play(signed_in, scorers):
    response = detail(signed_in, scorers["Rested"])

    assert response.context["latest_change"]["fp"] is None
    assert "No game since" in response.text


def test_the_player_page_without_two_snapshots_shows_a_dash(signed_in, season):
    player = record_days(make_player("Fresh"), season, [(30, 1)])

    response = detail(signed_in, player)

    assert response.context["latest_change"] is None
    assert response.status_code == 200


# --- the roster page ---------------------------------------------------------


def test_the_roster_shows_and_sorts_by_latest_fp(signed_in, manager, season, scorers):
    roster = Roster.objects.create(manager=manager, season=season, name="R")
    for player in scorers.values():
        services.buy(roster, player, player.current_salary)

    response = signed_in.get(
        reverse("fantasy:roster-build", args=[roster.pk]), {"sort": "latest", "dir": "desc"}
    )

    order = [membership.player.last_name for membership in response.context["memberships"]]
    assert order == ["High", "Low", "Zero", "Rested"]
    gains = {m.player.last_name: m.player.latest_fp_gain for m in response.context["memberships"]}
    assert gains == {"High": Decimal("60"), "Low": Decimal("10"), "Zero": 0, "Rested": None}
    # Table and phone cards both carry it.
    assert response.text.count("+60.0") == 2
