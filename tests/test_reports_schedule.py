"""The automatic Sunday report: `apps.reports.tasks.scheduled_weekly_run`."""

import pytest
from django_q.models import Schedule

from apps.nba.models import ImportRun
from apps.reports import tasks
from apps.reports.models import ReportRun, ReportSettings
from tests.reports_factories import FakeAi, at, make_season, make_world

pytestmark = pytest.mark.django_db

NOW = at(11, 15, 12, 30)


@pytest.fixture
def fake_ai(monkeypatch):
    fake = FakeAi()
    monkeypatch.setattr("apps.reports.ai.generate", fake)
    return fake


@pytest.fixture
def world(manager):
    season = make_season()
    make_world(season, manager)
    return season


@pytest.fixture
def sunday(django_capture_on_commit_callbacks):
    def sunday(**options):
        with django_capture_on_commit_callbacks(execute=True):
            return tasks.scheduled_weekly_run(now=NOW, **options)

    return sunday


def test_the_schedule_is_there():
    schedule = Schedule.objects.get(name="Sunday AI trade report")
    assert schedule.func == "apps.reports.tasks.scheduled_weekly_run"
    assert schedule.cron == "30 12 * * 0"


def test_sunday_writes_and_publishes_a_report_with_the_defaults(world, fake_ai, sunday):
    report_settings = ReportSettings.load()
    report_settings.default_model = "gpt-6.1-sol"
    report_settings.default_effort = "high"
    report_settings.save()

    result = sunday()

    run = ReportRun.objects.get()
    assert result.startswith(f"started run {run.pk}")
    assert (run.model, run.effort, run.trigger) == ("gpt-6.1-sol", "high", "scheduled")
    assert run.is_published
    assert run.started_by is None


def test_only_once_a_sunday(world, fake_ai, sunday):
    sunday()
    assert sunday() == "skipped: today's automatic run has already started"
    assert ReportRun.objects.count() == 1


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        (
            lambda s: ReportSettings.objects.update_or_create(
                pk=1, defaults={"sunday_run_enabled": False}
            ),
            "switched off",
        ),
        (lambda s: s.__class__.objects.update(is_current=False), "no current season"),
        (lambda s: s.__class__.objects.update(starts_on=at(12, 1).date()), "not running"),
    ],
)
def test_skips_with_a_reason(world, fake_ai, sunday, setup, reason):
    setup(world)
    assert reason in sunday()
    assert not ReportRun.objects.exists()
    assert fake_ai.calls == []


def test_skips_without_a_key(world, fake_ai, sunday, settings):
    settings.AI_BRIEFING_API_KEY = ""
    assert "no OpenAI API key" in sunday()
    assert not ReportRun.objects.exists()


def test_waits_for_an_import_still_running(world, fake_ai, sunday):
    ImportRun.objects.create(source=ImportRun.Source.SCHEDULED, started_at=at(11, 15, 12, 15))

    assert sunday() == "waiting for the stats import (1 of 6)"
    assert not ReportRun.objects.exists()
    retry = Schedule.objects.get(func=tasks.SUNDAY_TASK, schedule_type=Schedule.ONCE)
    assert retry.args == "(1,)"
    assert retry.next_run == at(11, 15, 12, 40)

    # After an hour of waiting it goes with the data there is.
    assert sunday(waited=6).startswith("started run")


def test_a_finished_or_manual_import_is_no_reason_to_wait(world, fake_ai, sunday):
    ImportRun.objects.create(
        source=ImportRun.Source.SCHEDULED,
        started_at=at(11, 15, 12, 15),
        status=ImportRun.Status.SUCCEEDED,
    )
    ImportRun.objects.create(source=ImportRun.Source.WEB, started_at=at(11, 15, 12, 20))
    assert sunday().startswith("started run")
