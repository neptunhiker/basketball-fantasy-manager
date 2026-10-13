"""Previewing, confirming and discarding a season stats upload, and the command."""

import datetime as dt
from decimal import Decimal
from unittest import mock

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from apps.nba.models import Player, PlayerSeasonStats, SeasonStatsImport
from apps.nba.season_stats import (
    PREVIEW_LIFETIME,
    PreviewClosed,
    SeasonStatsFileError,
    confirm_season_stats,
    discard_season_stats,
    expire_stale_previews,
    preview_season_stats,
    validate_season_label,
)

from .test_season_stats_parser import LUKA, line, stats_file

SEASON = "2025-26"


@pytest.fixture
def luka(db):
    return Player.objects.create(first_name="Luka", last_name="Doncic", position="G")


@pytest.fixture
def bam(db):
    return Player.objects.create(first_name="Bam", last_name="Adebayo", position="C")


def preview(*lines, user=None, season=SEASON):
    return preview_season_stats(
        stats_file(*lines), season=season, file_name="stats.csv", triggered_by=user
    )


def stats_of(player, season=SEASON):
    return PlayerSeasonStats.objects.get(player=player, season=season)


# --- season labels --------------------------------------------------------------------


@pytest.mark.parametrize("label", ["2025-26", "1999-00"])
def test_season_labels_name_two_consecutive_years(label):
    validate_season_label(label)


@pytest.mark.parametrize("label", ["", "2025", "2025-27", "2025/26", "25-26", "2025-2026"])
def test_other_season_labels_are_refused(label):
    with pytest.raises(SeasonStatsFileError, match="YYYY-YY"):
        validate_season_label(label)


# --- preview ----------------------------------------------------------------------------


def test_a_preview_counts_everything_and_writes_no_stats(luka, bam, user):
    run = preview(
        LUKA, line("Bam Adebayo"), line("Kyle Lowry", "PHI"), line("X", PTS="?"), user=user
    )

    assert run.status == SeasonStatsImport.Status.PREVIEWED
    assert (run.rows, run.matched, run.created, run.updated, run.removed) == (4, 2, 2, 0, 0)
    assert run.unmatched == ["Kyle Lowry (PHI)"]
    assert len(run.skipped) == 1
    assert run.triggered_by == user
    assert run.file_name == "stats.csv"
    assert len(run.payload) == 2
    assert not PlayerSeasonStats.objects.exists()


def test_a_preview_knows_what_an_import_would_update_and_remove(luka, bam):
    confirm_season_stats(preview(LUKA, line("Bam Adebayo")))

    run = preview(LUKA)

    assert (run.created, run.updated, run.removed) == (0, 1, 1)
    assert run.warnings == ["Bam Adebayo is not in the file; their 2025-26 stats will be removed."]


def test_a_bad_season_label_or_file_records_nothing(luka):
    with pytest.raises(SeasonStatsFileError):
        preview(LUKA, season="2025")
    with pytest.raises(SeasonStatsFileError):
        preview_season_stats(b"PLAYER;TEAM\r\n", season=SEASON)

    assert not SeasonStatsImport.objects.exists()


def test_a_file_with_only_a_header_is_refused(luka):
    with pytest.raises(SeasonStatsFileError, match="no players"):
        preview()


# --- confirm ----------------------------------------------------------------------------


def test_confirming_writes_every_matched_line(luka, bam, user):
    run = confirm_season_stats(preview(LUKA, line("Bam Adebayo", "MIA"), user=user), user)

    assert run.status == SeasonStatsImport.Status.IMPORTED
    assert run.imported_at is not None
    assert run.payload == []
    assert (run.created, run.updated, run.removed) == (2, 0, 0)
    stats = stats_of(luka)
    assert stats.source_name == "Luka Dončić"
    assert stats.team_abbreviation == "LAL"
    assert stats.import_run == run
    assert (stats.age, stats.games_played, stats.wins, stats.losses) == (27, 64, 43, 21)
    assert stats.points == Decimal("33.5")
    assert stats.three_pct == Decimal("36.6")
    assert stats.fantasy_points == Decimal("57.7")
    assert stats_of(bam).team_abbreviation == "MIA"


def test_a_second_upload_updates_and_removes(luka, bam):
    confirm_season_stats(preview(LUKA, line("Bam Adebayo")))
    first_id = stats_of(luka).pk

    run = confirm_season_stats(preview(line("Luka Dončić", "DAL", PTS="30.1")))

    assert (run.created, run.updated, run.removed) == (0, 1, 1)
    assert stats_of(luka).pk == first_id
    assert stats_of(luka).points == Decimal("30.1")
    assert stats_of(luka).team_abbreviation == "DAL"
    assert stats_of(luka).import_run == run
    assert not PlayerSeasonStats.objects.filter(player=bam).exists()


def test_other_seasons_are_left_alone(luka):
    confirm_season_stats(preview(LUKA, season="2024-25"))

    confirm_season_stats(preview(LUKA))

    assert PlayerSeasonStats.objects.filter(player=luka).count() == 2


def test_a_player_deleted_after_the_preview_is_left_out_with_a_warning(luka, bam):
    run = preview(LUKA, line("Bam Adebayo"))
    bam.delete()

    run = confirm_season_stats(run)

    assert run.created == 1
    assert run.warnings == ["Bam Adebayo was deleted after the preview and was left out."]


def test_a_preview_without_matches_cannot_wipe_the_season(luka):
    confirm_season_stats(preview(LUKA))
    run = preview(line("Kyle Lowry", "PHI"))

    with pytest.raises(PreviewClosed, match="nothing to import"):
        confirm_season_stats(run)

    assert stats_of(luka).points == Decimal("33.5")


def test_a_preview_is_confirmed_only_once(luka):
    run = confirm_season_stats(preview(LUKA))

    with pytest.raises(PreviewClosed, match="already been closed"):
        confirm_season_stats(run)


def test_only_the_uploader_can_confirm(luka, user, staff_user):
    run = preview(LUKA, user=user)

    with pytest.raises(PreviewClosed, match="person who uploaded"):
        confirm_season_stats(run, staff_user)
    assert not PlayerSeasonStats.objects.exists()


def test_an_old_preview_expires_instead_of_importing(luka):
    run = preview(LUKA)
    SeasonStatsImport.objects.filter(pk=run.pk).update(
        created_at=timezone.now() - PREVIEW_LIFETIME - dt.timedelta(minutes=1)
    )

    with pytest.raises(PreviewClosed, match="expired"):
        confirm_season_stats(run)

    run.refresh_from_db()
    assert run.status == SeasonStatsImport.Status.EXPIRED
    assert run.payload == []
    assert not PlayerSeasonStats.objects.exists()


def test_confirming_expires_other_open_previews_of_the_season(luka):
    older = preview(LUKA)
    other_season = preview(LUKA, season="2024-25")

    confirm_season_stats(preview(LUKA))

    older.refresh_from_db()
    other_season.refresh_from_db()
    assert older.status == SeasonStatsImport.Status.EXPIRED
    assert older.payload == []
    assert other_season.status == SeasonStatsImport.Status.PREVIEWED


def test_stale_previews_are_swept(luka):
    fresh = preview(LUKA)
    stale = preview(LUKA)
    SeasonStatsImport.objects.filter(pk=stale.pk).update(
        created_at=timezone.now() - PREVIEW_LIFETIME - dt.timedelta(minutes=1)
    )

    assert expire_stale_previews() == 1
    fresh.refresh_from_db()
    stale.refresh_from_db()
    assert (fresh.status, stale.status) == ("previewed", "expired")


def test_a_failure_while_writing_changes_nothing_and_is_recorded(luka, bam):
    confirm_season_stats(preview(LUKA))
    run = preview(line("Luka Dončić", PTS="1.0"), line("Bam Adebayo"))

    with (
        mock.patch.object(PlayerSeasonStats.objects, "bulk_update", side_effect=RuntimeError),
        pytest.raises(RuntimeError),
    ):
        confirm_season_stats(run)

    run.refresh_from_db()
    assert run.status == SeasonStatsImport.Status.FAILED
    assert run.payload == []
    assert stats_of(luka).points == Decimal("33.5")
    assert not PlayerSeasonStats.objects.filter(player=bam).exists()


# --- discard ----------------------------------------------------------------------------


def test_discarding_writes_nothing_and_drops_the_pending_rows(luka, user):
    run = discard_season_stats(preview(LUKA, user=user), user)

    assert run.status == SeasonStatsImport.Status.DISCARDED
    assert run.payload == []
    assert not PlayerSeasonStats.objects.exists()
    with pytest.raises(PreviewClosed):
        confirm_season_stats(run, user)


# --- the command ------------------------------------------------------------------------


@pytest.fixture
def csv_path(tmp_path):
    path = tmp_path / "stats.csv"
    path.write_bytes(stats_file(LUKA, line("Kyle Lowry", "PHI")))
    return path


def test_the_command_imports(luka, csv_path, capsys):
    call_command("import_season_stats", str(csv_path), "--season", SEASON)

    out = capsys.readouterr().out
    assert "Import finished: 2025-26, 2 lines; 1 matched, 1 new" in out
    assert "Kyle Lowry (PHI)" in out
    assert stats_of(luka).points == Decimal("33.5")
    assert SeasonStatsImport.objects.get().source == SeasonStatsImport.Source.COMMAND


def test_the_command_dry_run_saves_no_stats(luka, csv_path, capsys):
    call_command("import_season_stats", str(csv_path), "--season", SEASON, "--dry-run")

    assert "Dry run, nothing saved" in capsys.readouterr().out
    assert not PlayerSeasonStats.objects.exists()
    assert SeasonStatsImport.objects.get().status == SeasonStatsImport.Status.DISCARDED


def test_the_command_reports_an_unusable_file(db, tmp_path):
    with pytest.raises(CommandError, match="No such file"):
        call_command("import_season_stats", str(tmp_path / "nope.csv"), "--season", SEASON)
    with pytest.raises(CommandError, match="YYYY-YY"):
        call_command("import_season_stats", __file__, "--season", "2025")
