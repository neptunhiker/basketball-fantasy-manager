"""Turning basketball.de rows into players and snapshots.

A fake client stands in for the site, so these tests are about the decisions
-- who a row is, what changes, who is retired -- not about HTML.
"""

import datetime as dt
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.fantasy.models import PlayerSnapshot, Season
from apps.nba.bbde import BbdeLoginError, BbdePage, BbdeRow, BbdeSessionExpired, parse_page
from apps.nba.importer import ImportAlreadyRunning, NoCurrentSeason, import_bbde
from apps.nba.models import ImportRun, Player, Team

FIXTURES = Path(__file__).parent / "fixtures" / "bbde"


def row(
    first,
    last,
    team="Miami Heat",
    *,
    bbde_id=None,
    position="F",
    salary="5000000",
    total_fp="0",
    fp_per_game="0",
    games=0,
    rookie=False,
    consistent=True,
):
    return BbdeRow(
        bbde_id=bbde_id,
        first_name=first,
        last_name=last,
        team_name=team,
        is_rookie=rookie,
        position=position,
        salary=Decimal(salary),
        total_fp=Decimal(total_fp),
        fp_per_game=Decimal(fp_per_game),
        games_played=games,
        games_consistent=consistent,
    )


class FakeClient:
    def __init__(self, *pages, login_error=None, page_error=None):
        self.pages = list(pages)
        self.login_error = login_error
        self.page_error = page_error
        self.logins = []

    def login(self, username, password):
        self.logins.append((username, password))
        if self.login_error:
            raise self.login_error

    def iter_pages(self):
        yield from self.pages
        if self.page_error:
            raise self.page_error


def pages_of(*rows):
    return BbdePage(rows=list(rows))


def run_import(*rows, **kwargs):
    client = kwargs.pop("client", None) or FakeClient(pages_of(*rows))
    return import_bbde("coach", "s3cret", client=client, **kwargs)


@pytest.fixture
def season(db):
    return Season.objects.create(
        label="2026-27",
        starts_on=dt.date(2026, 10, 20),
        ends_on=dt.date(2027, 4, 11),
        is_current=True,
    )


@pytest.fixture
def teams(db):
    call_command("seed_teams", verbosity=0)
    return {team.abbreviation: team for team in Team.objects.all()}


@pytest.fixture
def bam(teams):
    return Player.objects.create(
        first_name="Bam", last_name="Adebayo", position="C", team=teams["MIA"]
    )


# --- matching -------------------------------------------------------------------


def test_the_first_import_links_an_existing_player_by_name_and_team(season, bam):
    run = run_import(row("Bam", "Adebayo", bbde_id=1153, position="C", salary="10170000"))

    bam.refresh_from_db()
    assert bam.bbde_id == 1153
    assert bam.current_salary == Decimal("10170000.00")
    assert (run.matched, run.created) == (1, 0)


def test_later_imports_match_by_id_even_after_a_trade_and_a_respelling(season, bam, teams):
    bam.bbde_id = 1153
    bam.save()

    run_import(row("Bam", "Adebayo-Jr", "Boston Celtics", bbde_id=1153, position="C"))

    bam.refresh_from_db()
    assert bam.team == teams["BOS"]
    assert Player.objects.count() == 1


def test_accents_do_not_stop_a_name_match(season, teams):
    luka = Player.objects.create(
        first_name="Luka", last_name="Dončić", position="G", team=teams["LAL"]
    )

    run_import(row("Luka", "Doncic", "Los Angeles Lakers", bbde_id=1, position="G"))

    luka.refresh_from_db()
    assert luka.bbde_id == 1


def test_a_unique_name_matches_even_when_the_team_changed(season, bam):
    run_import(row("Bam", "Adebayo", "Boston Celtics", bbde_id=1153, position="C"))

    bam.refresh_from_db()
    assert bam.bbde_id == 1153
    assert bam.team.abbreviation == "BOS"


def test_two_players_with_the_same_name_are_skipped_not_guessed(season, teams):
    for team in ("MIA", "BOS"):
        Player.objects.create(
            first_name="Chris", last_name="Johnson", position="F", team=teams[team]
        )

    run = run_import(row("Chris", "Johnson", "Denver Nuggets", bbde_id=7))

    assert run.ambiguous == ["Chris Johnson (Denver Nuggets): chris-johnson, chris-johnson-2"]
    assert not Player.objects.filter(bbde_id=7).exists()
    assert PlayerSnapshot.objects.count() == 0


def test_the_team_breaks_a_tie_between_two_namesakes(season, teams):
    Player.objects.create(first_name="Chris", last_name="Johnson", position="F", team=teams["MIA"])
    boston = Player.objects.create(
        first_name="Chris", last_name="Johnson", position="F", team=teams["BOS"]
    )

    run_import(row("Chris", "Johnson", "Boston Celtics", bbde_id=7))

    boston.refresh_from_db()
    assert boston.bbde_id == 7


def test_unknown_players_are_created_with_their_id_and_rookie_flag(season, teams):
    run = run_import(
        row("Caleb", "Wilson", "Chicago Bulls", bbde_id=4532, salary="4500000", rookie=True)
    )

    player = Player.objects.get(bbde_id=4532)
    assert (player.first_name, player.last_name, player.team) == ("Caleb", "Wilson", teams["CHI"])
    assert player.is_rookie
    assert player.current_salary == Decimal("4500000.00")
    assert run.created == 1
    assert run.created_players == ["Caleb Wilson (Chicago Bulls)"]


def test_free_agents_have_no_team(season, bam):
    run_import(row("Bam", "Adebayo", "Free Agents", bbde_id=1153, position="C"))

    bam.refresh_from_db()
    assert bam.team is None


def test_the_clippers_are_found_under_their_long_name(season, teams):
    run_import(row("Kawhi", "Leonard", "Los Angeles Clippers", bbde_id=5))

    assert Player.objects.get(bbde_id=5).team == teams["LAC"]


def test_an_unknown_team_skips_the_row(season, teams):
    run = run_import(row("Some", "One", "Seattle SuperSonics", bbde_id=5))

    assert run.skipped == ["Some One: unknown team 'Seattle SuperSonics'"]
    assert not Player.objects.exists()


def test_a_player_listed_twice_is_imported_once(season, teams):
    client = FakeClient(
        pages_of(row("Bam", "Adebayo", bbde_id=1153)),
        pages_of(row("Bam", "Adebayo", bbde_id=1153)),
    )

    run = run_import(client=client)

    assert run.rows == 1
    assert Player.objects.count() == 1


# --- snapshots ------------------------------------------------------------------


def test_each_run_records_its_own_snapshot(season, bam):
    first = run_import(row("Bam", "Adebayo", bbde_id=1153, position="C", salary="10000000"))
    second = run_import(
        row(
            "Bam",
            "Adebayo",
            bbde_id=1153,
            position="C",
            salary="10500000",
            total_fp="72.50",
            fp_per_game="36.25",
            games=2,
        )
    )

    snapshots = list(bam.snapshots.order_by("as_of"))
    assert [s.as_of for s in snapshots] == [first.started_at, second.started_at]
    assert all(s.season == season for s in snapshots)
    bam.refresh_from_db()
    assert bam.current_salary == Decimal("10500000.00")
    assert bam.current_games_played == 2
    assert bam.current_fp_per_game == Decimal("36.25")


def test_inexact_games_played_is_kept_with_a_warning(season, bam):
    run = run_import(
        row("Bam", "Adebayo", bbde_id=1153, total_fp="10", fp_per_game="0", consistent=False)
    )

    assert bam.snapshots.count() == 1
    assert run.warnings == [
        "Bam Adebayo: games played could not be derived exactly (FP 10, FP/G 0)."
    ]


# --- who is still in the league -------------------------------------------------


def test_players_who_drop_off_the_list_are_marked_inactive(season, bam, teams):
    gone = Player.objects.create(
        first_name="Gone", last_name="Guy", position="G", team=teams["MIA"], bbde_id=99
    )

    run = run_import(row("Bam", "Adebayo", bbde_id=1153))

    gone.refresh_from_db()
    assert not gone.is_active
    assert run.inactivated == 1


def test_players_never_linked_to_basketball_de_are_left_alone(season, bam, teams):
    local = Player.objects.create(first_name="Local", last_name="Only", position="G")

    run_import(row("Bam", "Adebayo", bbde_id=1153))

    local.refresh_from_db()
    assert local.is_active


def test_a_returning_player_is_reactivated(season, bam):
    bam.bbde_id = 1153
    bam.is_active = False
    bam.save()

    run = run_import(row("Bam", "Adebayo", bbde_id=1153))

    bam.refresh_from_db()
    assert bam.is_active
    assert run.reactivated == 1


def test_a_much_shorter_list_does_not_retire_anyone(season, teams):
    rows = [row(f"P{i}", "Player", bbde_id=i) for i in range(10)]
    run_import(*rows)

    run = run_import(*rows[:5])

    assert Player.objects.filter(is_active=True).count() == 10
    assert run.inactivated == 0
    assert "so no player was marked inactive" in run.warnings[0]


# --- the run itself ---------------------------------------------------------------


def test_a_successful_run_is_recorded_without_the_login(season, bam, staff_user):
    run = run_import(row("Bam", "Adebayo", bbde_id=1153), triggered_by=staff_user)

    run.refresh_from_db()
    assert run.status == ImportRun.Status.SUCCEEDED
    assert run.triggered_by == staff_user
    assert run.season == season
    assert run.finished_at is not None
    stored = " ".join(str(value) for value in ImportRun.objects.values().get(pk=run.pk).values())
    assert "s3cret" not in stored
    assert "coach" not in stored


def test_a_rejected_login_marks_the_run_failed(season):
    client = FakeClient(login_error=BbdeLoginError("not accepted"))

    with pytest.raises(BbdeLoginError):
        import_bbde("coach", "s3cret", client=client)

    run = ImportRun.objects.get()
    assert run.status == ImportRun.Status.FAILED
    assert run.error == "not accepted"


def test_a_failure_halfway_through_the_pages_writes_nothing(season, teams):
    client = FakeClient(
        pages_of(row("Bam", "Adebayo", bbde_id=1153)),
        page_error=BbdeSessionExpired("login page"),
    )

    with pytest.raises(BbdeSessionExpired):
        import_bbde("coach", "s3cret", client=client)

    assert not Player.objects.exists()
    assert ImportRun.objects.get().status == ImportRun.Status.FAILED


def test_a_dry_run_reports_but_saves_nothing(season, teams):
    run = run_import(row("Caleb", "Wilson", "Chicago Bulls", bbde_id=4532), dry_run=True)

    assert run.created == 1
    assert run.dry_run
    assert not Player.objects.exists()
    assert not PlayerSnapshot.objects.exists()


def test_there_must_be_a_current_season(db, teams):
    with pytest.raises(NoCurrentSeason):
        run_import(row("Bam", "Adebayo", bbde_id=1153))

    assert not ImportRun.objects.exists()


def test_two_imports_cannot_run_at_once(season):
    ImportRun.objects.create(source=ImportRun.Source.WEB)

    with pytest.raises(ImportAlreadyRunning):
        run_import()


def test_a_run_that_died_long_ago_does_not_block_the_next(season, teams):
    ImportRun.objects.create(
        source=ImportRun.Source.WEB, started_at=timezone.now() - timedelta(hours=1)
    )

    run = run_import(row("Bam", "Adebayo", bbde_id=1153))

    assert run.status == ImportRun.Status.SUCCEEDED


def test_the_saved_pages_import_end_to_end(season, teams):
    pages = [
        parse_page((FIXTURES / f"page_{name}.html").read_text(encoding="utf-8"))
        for name in ("first", "middle", "last")
    ]

    run = run_import(client=FakeClient(*pages))

    assert run.rows == 62
    assert run.created == 62
    assert run.skipped == []
    # The first page announces 31 pages; the fake client only has three.
    assert run.warnings == ["The pager announced 31 pages but 3 were read."]
    assert Player.objects.get(bbde_id=1153).current_salary == Decimal("10170000.00")
    assert Player.objects.get(bbde_id=4532).is_rookie
    assert Player.objects.get(bbde_id=4503).team is None
