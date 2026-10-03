"""`manage.py reset_game_data`: a clean slate for a new game, with a guard rail."""

import datetime as dt
from decimal import Decimal
from unittest import mock

import pytest
from django.core.management import CommandError, call_command
from django.db import connection

from apps.fantasy import services
from apps.fantasy.models import Manager, PlayerSnapshot, Roster, Season, TradeGrant, Transaction
from apps.nba.models import ImportRun, Player, PlayerInjury, PlayerNote, Team, TeamNote

INPUT = "apps.fantasy.management.commands.reset_game_data.input"


@pytest.fixture
def game(user, manager):
    """A little of everything the command deletes, and of everything it keeps."""
    call_command("seed_teams", verbosity=0)
    season = Season.objects.create(
        label="2026-27", starts_on=dt.date(2026, 10, 21), ends_on=dt.date(2027, 4, 11)
    )
    TradeGrant.objects.create(
        season=season,
        label="Helpside Trade",
        granted_at=dt.datetime(2026, 11, 1, 21, tzinfo=dt.UTC),
    )
    team = Team.objects.get(abbreviation="MIA")
    player = Player.objects.create(last_name="Adebayo", position="C", team=team)
    services.record_snapshot(
        player, season, dt.datetime(2026, 10, 1, tzinfo=dt.UTC), 10_000_000, 0, 0
    )
    roster = Roster.objects.create(manager=manager, season=season, name="R")
    services.buy(roster, player, Decimal("10000000"))
    PlayerInjury.objects.create(
        player=player, observed_at=dt.datetime(2026, 10, 1, tzinfo=dt.UTC), status="Out"
    )
    PlayerNote.objects.create(user=user, player=player, content="Watch him")
    TeamNote.objects.create(user=user, team=team, content="Deep bench")
    services.add_to_watchlist(user, player)
    ImportRun.objects.create(source=ImportRun.Source.COMMAND, status=ImportRun.Status.SUCCEEDED)
    return season


def test_a_dry_run_only_counts(game, capsys):
    call_command("reset_game_data", "--dry-run")

    output = capsys.readouterr().out
    assert "Players                  1" in output
    assert "Dry run" in output
    assert Player.objects.count() == 1


def test_the_wrong_answer_deletes_nothing(game):
    with mock.patch(INPUT, return_value="production"), pytest.raises(CommandError):
        call_command("reset_game_data")

    assert Roster.objects.count() == 1
    assert Player.objects.count() == 1


def test_confirming_with_the_database_name_wipes_the_game_but_keeps_the_frame(game, user):
    with mock.patch(INPUT, return_value=connection.settings_dict["NAME"]):
        call_command("reset_game_data")

    for model in (Transaction, Roster, Player, PlayerSnapshot, PlayerInjury, PlayerNote, ImportRun):
        assert not model.objects.exists(), model
    assert not user.watchlist_entries.exists()
    assert Team.objects.count() == 30
    assert Season.objects.count() == 1
    assert TradeGrant.objects.count() == 1
    assert Manager.objects.count() == 1
    assert TeamNote.objects.count() == 1
    user.refresh_from_db()
