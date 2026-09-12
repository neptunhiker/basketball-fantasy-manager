import datetime as dt
from decimal import Decimal

import pytest
from django.db.utils import IntegrityError
from django.utils import timezone

from apps.fantasy import services
from apps.fantasy.models import PlayerSnapshot, Season
from apps.nba.models import Player, Team


@pytest.fixture
def season(db):
    return Season.objects.create(
        label="2025-26",
        starts_on=dt.date(2025, 10, 21),
        ends_on=dt.date(2026, 4, 12),
        is_current=True,
    )


@pytest.fixture
def teams(db):
    from django.core.management import call_command

    call_command("seed_teams", verbosity=0)
    return {t.abbreviation: t for t in Team.objects.all()}


@pytest.fixture
def player(teams):
    return Player.objects.create(
        first_name="Traded",
        last_name="Guy",
        position=Player.Position.FORWARD,
        team=teams["LAL"],
    )


def record(player, season, days_ago=0, **kwargs):
    defaults = {
        "salary": 10_000_000,
        "total_fp": Decimal("400.00"),
        "games_played": 20,
        "team": player.team,
        "position": player.position,
    }
    defaults.update(kwargs)
    return services.record_snapshot(
        player, season, timezone.now() - dt.timedelta(days=days_ago), **defaults
    )


# --- the row itself ----------------------------------------------------------


def test_recording_a_snapshot_stores_the_values(player, season):
    snapshot, created = record(player, season, salary=12_500_000, total_fp="512.50")
    assert created
    assert snapshot.salary == Decimal("12500000.00")
    assert snapshot.total_fp == Decimal("512.50")


def test_fp_per_game_is_computed_by_the_database(player, season):
    snapshot, _ = record(player, season, total_fp="500.00", games_played=20)
    snapshot.refresh_from_db()
    assert snapshot.fp_per_game == Decimal("25.00")


def test_fp_per_game_is_null_before_the_first_game(player, season):
    snapshot, _ = record(player, season, total_fp="0.00", games_played=0)
    snapshot.refresh_from_db()
    assert snapshot.fp_per_game is None


def test_fp_per_game_cannot_be_written_directly(player, season):
    """It is a generated column, so it always follows the two inputs."""
    snapshot, _ = record(player, season, total_fp="300.00", games_played=10)
    PlayerSnapshot.objects.filter(pk=snapshot.pk).update(total_fp=Decimal("600.00"))
    snapshot.refresh_from_db()
    assert snapshot.fp_per_game == Decimal("60.00")


def test_the_same_moment_cannot_be_recorded_twice(player, season):
    moment = timezone.now()
    PlayerSnapshot.objects.create(
        player=player,
        season=season,
        as_of=moment,
        salary=1,
        total_fp=1,
        games_played=1,
        position="F",
    )
    with pytest.raises(IntegrityError):
        PlayerSnapshot.objects.create(
            player=player,
            season=season,
            as_of=moment,
            salary=2,
            total_fp=2,
            games_played=2,
            position="F",
        )


def test_reimporting_the_same_moment_updates_in_place(player, season):
    moment = timezone.now()
    services.record_snapshot(player, season, moment, 1_000_000, "10.00", 1)
    snapshot, created = services.record_snapshot(player, season, moment, 2_000_000, "20.00", 2)

    assert not created
    assert PlayerSnapshot.objects.count() == 1
    assert snapshot.salary == Decimal("2000000.00")


# --- querying ----------------------------------------------------------------


def test_latest_per_player_returns_one_row_each(season, teams):
    a = Player.objects.create(first_name="A", last_name="One", position="G")
    b = Player.objects.create(first_name="B", last_name="Two", position="C")
    record(a, season, days_ago=5, salary=1_000_000)
    record(a, season, days_ago=0, salary=3_000_000)
    record(b, season, days_ago=2, salary=2_000_000)

    latest = {s.player: s.salary for s in PlayerSnapshot.objects.latest_per_player()}
    assert latest == {a: Decimal("3000000.00"), b: Decimal("2000000.00")}


def test_as_of_ignores_snapshots_from_the_future(player, season):
    record(player, season, days_ago=10, salary=1_000_000)
    record(player, season, days_ago=0, salary=9_000_000)

    cutoff = timezone.now() - dt.timedelta(days=5)
    [snapshot] = PlayerSnapshot.objects.as_of(cutoff)
    assert snapshot.salary == Decimal("1000000.00")


# --- keeping Player in step --------------------------------------------------


def test_a_newer_snapshot_moves_the_player_to_the_new_team(player, season, teams):
    record(player, season, days_ago=0, team=teams["BOS"])
    player.refresh_from_db()
    assert player.team == teams["BOS"]


def test_an_older_snapshot_does_not_drag_the_team_backwards(player, season, teams):
    """The trap this guard exists for: backfilling archived pages."""
    record(player, season, days_ago=0, team=teams["BOS"])
    player.refresh_from_db()
    assert player.team == teams["BOS"]

    # Now import an archived page from before the trade.
    record(player, season, days_ago=30, team=teams["LAL"])
    player.refresh_from_db()
    assert player.team == teams["BOS"]


def test_a_reclassified_position_reaches_the_player(player, season):
    assert player.position == Player.Position.FORWARD
    record(player, season, position=Player.Position.CENTER)
    player.refresh_from_db()
    assert player.position == Player.Position.CENTER


def test_the_snapshot_keeps_the_team_the_source_reported(player, season, teams):
    old, _ = record(player, season, days_ago=30, team=teams["LAL"])
    new, _ = record(player, season, days_ago=0, team=teams["BOS"])

    assert old.team == teams["LAL"]
    assert new.team == teams["BOS"]
    player.refresh_from_db()
    assert player.team == teams["BOS"]


def test_syncing_reports_what_it_changed(player, season, teams):
    record(player, season, team=teams["BOS"], position=Player.Position.CENTER)
    player.refresh_from_db()
    # Already in step, so a second sync changes nothing.
    assert services.sync_player_from_latest_snapshot(player) == []
