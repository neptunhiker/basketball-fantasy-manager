"""Hotness is stored on the player and kept in step with his snapshots.

The rules for counting live in `apps.nba.stats.hotness_counts` and are tested
through `Player.hotness_score` in test_nba_models.py. These tests are about the
storing: every path that changes snapshots refreshes the figure, and the
player list sorts by it in the database instead of in Python.
"""

import datetime as dt
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.fantasy import services
from apps.fantasy.models import PlayerSnapshot, Season
from apps.nba.models import Player


@pytest.fixture
def season(db):
    return Season.objects.create(
        label="2026-27",
        starts_on=dt.date(2026, 10, 20),
        ends_on=dt.date(2027, 4, 11),
        is_current=True,
    )


@pytest.fixture
def signed_in(client, user, password):
    client.login(email=user.email, password=password)
    return client


def record(player, season, averages, start=None):
    """One snapshot per game through the import's own service."""
    start = start or timezone.now() - dt.timedelta(days=len(averages))
    for games, average in enumerate(averages, start=1):
        services.record_snapshot(
            player,
            season,
            start + dt.timedelta(days=games),
            salary=1_000_000,
            total_fp=average * games,
            games_played=games,
            # The import mirrors the snapshot's team onto the player.
            team=player.team,
        )


def make_player(last_name, **extra):
    return Player.objects.create(
        first_name="P", last_name=last_name, position=Player.Position.GUARD, **extra
    )


def test_the_import_stores_the_figure(season):
    player = make_player("Riser")

    record(player, season, [10, 12, 11, 14])

    player.refresh_from_db()
    assert (player.hotness_increases, player.hotness_comparisons) == (2, 3)
    assert player.hotness_score() == "2/3"


def test_the_instance_in_hand_is_updated_too(season):
    """No reload needed: the snapshot's own player object gets the counts."""
    player = make_player("InHand")

    PlayerSnapshot.objects.create(
        player=player, season=season, as_of=timezone.now(), salary=1, total_fp=10, games_played=1
    )
    PlayerSnapshot.objects.create(
        player=player,
        season=season,
        as_of=timezone.now() + dt.timedelta(days=1),
        salary=1,
        total_fp=30,
        games_played=2,
    )

    assert player.hotness_score() == "1/1"


def test_a_corrected_snapshot_recounts(season):
    """update_or_create on the same moment is a save too."""
    player = make_player("Corrected")
    start = timezone.now() - dt.timedelta(days=5)
    record(player, season, [10, 12], start=start)
    player.refresh_from_db()
    assert player.hotness_score() == "1/1"

    # The second game's figures arrive corrected: the average fell instead.
    services.record_snapshot(
        player, season, start + dt.timedelta(days=2), salary=1, total_fp=16, games_played=2
    )

    player.refresh_from_db()
    assert player.hotness_score() == "0/1"


def test_no_games_means_no_score(season):
    player = make_player("Bench")

    record(player, season, [])

    assert player.hotness_score() is None


def test_deleting_a_season_recounts_the_players_who_had_prices_in_it(client, staff_user, password):
    staff_user.is_superuser = True
    staff_user.save(update_fields=["is_superuser"])
    client.login(email=staff_user.email, password=password)
    older = Season.objects.create(
        label="2025-26", starts_on=dt.date(2025, 10, 20), ends_on=dt.date(2026, 4, 11)
    )
    newer = Season.objects.create(
        label="2026-27", starts_on=dt.date(2026, 10, 20), ends_on=dt.date(2027, 4, 11)
    )
    player = make_player("Twice")
    record(player, older, [10, 12, 14], start=timezone.now() - dt.timedelta(days=30))
    record(player, newer, [10, 8])
    player.refresh_from_db()
    assert player.hotness_score() == "0/1"

    client.post(
        reverse("fantasy:season-delete", args=[newer.pk]),
        {"confirm": "DELETE"},
        headers={"HX-Request": "true"},
    )

    player.refresh_from_db()
    assert player.hotness_score() == "2/2"


def test_the_command_repairs_figures_written_around_the_signal(season):
    """bulk_create skips post_save, like loaddata does."""
    player = make_player("Bulk")
    now = timezone.now()
    PlayerSnapshot.objects.bulk_create(
        PlayerSnapshot(
            player=player,
            season=season,
            as_of=now + dt.timedelta(days=games),
            salary=1,
            total_fp=average * games,
            games_played=games,
        )
        for games, average in enumerate([10, 12, 14], start=1)
    )
    player.refresh_from_db()
    assert player.hotness_score() is None

    out = StringIO()
    call_command("refresh_hotness", stdout=out)

    player.refresh_from_db()
    assert player.hotness_score() == "2/2"
    assert "1 player(s) changed" in out.getvalue()


# --- sorting -------------------------------------------------------------------


def names(response):
    return [player.last_name for player in response.context["players"]]


def test_sorting_by_hotness_happens_in_the_database(signed_in, season):
    hot = make_player("Hot")
    record(hot, season, [10, 11, 12, 13])  # 3/3
    cold = make_player("Cold")
    record(cold, season, [13, 12, 11, 10])  # 0/3
    mixed = make_player("Mixed")
    record(mixed, season, [10, 12, 11])  # 1/2
    make_player("Unscored")

    url = reverse("nba:player-list")
    descending = signed_in.get(url, {"sort": "hotness", "dir": "desc"})
    ascending = signed_in.get(url, {"sort": "hotness", "dir": "asc"})

    assert names(descending) == ["Hot", "Mixed", "Cold", "Unscored"]
    # A player without a score is last in both directions.
    assert names(ascending) == ["Cold", "Mixed", "Hot", "Unscored"]
    # Paginated by the database, not sliced from a list: a Page of a QuerySet.
    assert hasattr(descending.context["page_obj"].paginator.object_list, "query")


def test_equal_shares_rank_the_longer_record_first_then_by_name(signed_in, season):
    for last_name, averages in [
        ("Brown", [10, 12, 11]),  # 1/2
        ("Adams", [10, 12, 11]),  # 1/2
        ("Long", [10, 12, 11, 13, 12]),  # 2/4
    ]:
        record(make_player(last_name), season, averages)

    response = signed_in.get(reverse("nba:player-list"), {"sort": "hotness", "dir": "desc"})

    assert names(response) == ["Long", "Adams", "Brown"]


def test_the_team_page_sorts_the_same_way(signed_in, season):
    from apps.nba.models import Team

    team = Team.objects.create(name="Testers", abbreviation="TST")
    record(make_player("Hot", team=team), season, [10, 11, 12])
    record(make_player("Cold", team=team), season, [12, 11, 10])

    response = signed_in.get(
        reverse("nba:team-detail", args=["TST"]), {"sort": "hotness", "dir": "desc"}
    )

    assert names(response) == ["Hot", "Cold"]


# --- what it saves ----------------------------------------------------------------


def test_the_player_list_does_not_load_snapshots(signed_in, season):
    """The point of storing it: a season of daily rows is never read per request.

    One fixed lookup stays -- `salary_baseline` asks for the newest read
    moment, LIMIT 1 -- but nothing fetches snapshot rows for the players.
    """
    for index in range(3):
        record(make_player(f"P{index}"), season, [10, 12, 11, 13, 15, 14, 16])

    with CaptureQueriesContext(connection) as captured:
        signed_in.get(reverse("nba:player-list"), {"sort": "hotness"})

    prefetches = [
        query["sql"]
        for query in captured.captured_queries
        if '"fantasy_playersnapshot"."player_id" IN (' in query["sql"]
    ]
    assert prefetches == []
