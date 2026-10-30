"""When the Dream Team is worked out: after each import, and on request."""

import datetime as dt
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from apps.analytics.models import DreamTeam
from apps.fantasy.models import PlayerSnapshot, Season
from apps.nba.models import ImportRun, Player, Team

from .test_bbde_import import row, run_import

pytestmark = pytest.mark.django_db

# Six Guards, six Forwards, three Centers: one legal team of 15 and no more.
LINEUP = [("G", n) for n in range(6)] + [("F", n) for n in range(6)] + [("C", n) for n in range(3)]


@pytest.fixture
def season(db):
    today = timezone.localdate()
    return Season.objects.create(
        label="2026-27",
        starts_on=today - dt.timedelta(days=10),
        ends_on=today + dt.timedelta(days=150),
        is_current=True,
    )


@pytest.fixture
def drafted(season):
    """Fifteen players with a $4M price from before the season started."""
    team = Team.objects.get(abbreviation="MIA")
    preseason = timezone.now() - dt.timedelta(days=12)
    players = []
    for index, (position, number) in enumerate(LINEUP, start=1):
        player = Player.objects.create(
            first_name="P", last_name=f"{position}{number}", position=position, team=team
        )
        player.bbde_id = 1000 + index
        player.save()
        PlayerSnapshot.objects.create(
            player=player,
            season=season,
            as_of=preseason,
            salary=Decimal("4000000"),
            total_fp=0,
            games_played=0,
            position=position,
            team=team,
        )
        players.append(player)
    return players


def rows_for(players, games=2):
    return [
        row(
            "P",
            p.last_name,
            bbde_id=p.bbde_id,
            position=p.position,
            salary="4000000",
            total_fp=str(10 * games),
            games=games,
        )
        for p in players
    ]


# --- after an import ----------------------------------------------------------------


def test_an_import_works_out_the_dream_team_for_its_own_snapshots(season, drafted):
    run = run_import(*rows_for(drafted))

    dream_team = DreamTeam.objects.get()
    assert dream_team.as_of == run.started_at
    assert dream_team.picks.count() == 15
    assert run.warnings == []


def test_a_dry_run_leaves_the_dream_team_alone(season, drafted):
    run_import(*rows_for(drafted), dry_run=True)

    assert not DreamTeam.objects.exists()


def test_before_the_first_game_an_import_stores_no_dream_team_and_no_warning(season, drafted):
    run = run_import(*rows_for(drafted, games=0))

    run.refresh_from_db()
    assert run.status == ImportRun.Status.SUCCEEDED
    assert run.warnings == []
    assert not DreamTeam.objects.exists()


def test_a_dream_team_failure_never_fails_the_import(season, drafted, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("solver exploded")

    monkeypatch.setattr("apps.nba.importer.refresh_dream_team", broken)

    run = run_import(*rows_for(drafted))

    run.refresh_from_db()
    assert run.status == ImportRun.Status.SUCCEEDED
    assert PlayerSnapshot.objects.filter(as_of=run.started_at).count() == 15
    assert any("Dream Team" in warning for warning in run.warnings)


# --- the command ----------------------------------------------------------------------


def import_twice(drafted):
    """Two imports a day apart, the second with more points."""
    first = run_import(*rows_for(drafted, games=1))
    PlayerSnapshot.objects.filter(as_of=first.started_at).update(
        as_of=first.started_at - dt.timedelta(days=1)
    )
    DreamTeam.objects.all().delete()
    run_import(*rows_for(drafted, games=2))
    DreamTeam.objects.all().delete()


def test_all_stores_one_result_per_import_after_draft_day(season, drafted):
    import_twice(drafted)
    out = StringIO()

    call_command("refresh_dream_team", "--all", stdout=out)

    assert DreamTeam.objects.count() == 2
    output = out.getvalue()
    assert "no Dream Team" in output  # the pre-season snapshot
    assert "first of the season" in output
    assert "0 in, 0 out" in output
    assert "2 Dream Team(s) stored" in output


def test_running_it_twice_changes_nothing(season, drafted):
    import_twice(drafted)
    call_command("refresh_dream_team", "--all", stdout=StringIO())
    before = list(DreamTeam.objects.values_list("pk", "as_of", "total_fp"))

    call_command("refresh_dream_team", "--all", stdout=StringIO())

    assert list(DreamTeam.objects.values_list("pk", "as_of", "total_fp")) == before


def test_without_all_only_the_newest_import_is_worked_out(season, drafted):
    import_twice(drafted)

    call_command("refresh_dream_team", stdout=StringIO())

    assert DreamTeam.objects.count() == 1


def test_an_unknown_season_is_an_error(season):
    with pytest.raises(CommandError, match="no season 1999-00"):
        call_command("refresh_dream_team", "--season", "1999-00")


def test_without_a_current_season_a_season_must_be_named(season):
    Season.objects.update(is_current=False)

    with pytest.raises(CommandError, match="--season"):
        call_command("refresh_dream_team")
