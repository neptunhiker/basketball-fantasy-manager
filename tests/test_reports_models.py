"""The AI trade report models: settings row, runs, reports, recommended trades."""

from decimal import Decimal
from io import StringIO
from unittest import mock

import pytest
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction

from apps.reports.models import RecommendedTrade, ReportRun, ReportSettings
from tests.reports_factories import make_player, make_report, make_roster, make_run, make_season

pytestmark = pytest.mark.django_db


def test_settings_row_is_made_on_first_read_with_catalog_defaults():
    settings_row = ReportSettings.load()
    assert settings_row.pk == 1
    assert settings_row.default_model == "gpt-6-luna"
    assert settings_row.default_effort == "medium"
    assert settings_row.sunday_run_enabled is True
    assert ReportSettings.load().pk == 1
    assert ReportSettings.objects.count() == 1


def test_there_is_only_ever_one_settings_row():
    ReportSettings.load()
    with pytest.raises(IntegrityError), transaction.atomic():
        ReportSettings.objects.create(id=2)


def test_one_briefing_per_run(manager):
    run = make_run(make_season())
    make_report(run)
    with pytest.raises(IntegrityError), transaction.atomic():
        make_report(run)


def test_one_report_per_roster_per_run(manager):
    season = make_season()
    run = make_run(season)
    roster = make_roster(season, manager)
    make_report(run, roster)
    with pytest.raises(IntegrityError), transaction.atomic():
        make_report(run, roster)
    # Another run may report on the same roster.
    make_report(make_run(season), roster)


def test_a_report_outlives_its_roster(manager):
    season = make_season()
    run = make_run(season)
    roster = make_roster(season, manager)
    report = make_report(run, roster)
    roster.delete()
    report.refresh_from_db()
    assert report.roster is None


def test_run_names_its_model_even_after_leaving_the_catalog():
    run = make_run(make_season(), model="gpt-6.1-sol")
    assert run.model_name == "GPT-6.1 Sol"
    run.model = "gpt-retired"
    assert run.model_name == "gpt-retired"
    assert run.is_active
    run.status = ReportRun.Status.PARTIAL
    assert not run.is_active


def test_reset_game_data_removes_runs_before_the_players_they_name(manager):
    season = make_season()
    out, in_ = make_player(), make_player()
    run = make_run(season)
    report = make_report(run, make_roster(season, manager, [out]))
    RecommendedTrade.objects.create(
        report=report,
        rank=1,
        trade_ref="T01",
        player_out=out,
        player_in=in_,
        salary_out=Decimal("3000000"),
        salary_in=Decimal("3000000"),
        confidence="high",
    )
    with mock.patch(
        "apps.fantasy.management.commands.reset_game_data.input",
        return_value=connection.settings_dict["NAME"],
    ):
        call_command("reset_game_data", stdout=StringIO())
    assert not ReportRun.objects.exists()
