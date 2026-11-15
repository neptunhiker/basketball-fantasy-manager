"""The daily 12:20 injury import: `apps.nba.tasks.scheduled_injury_import`."""

import datetime as dt

import pytest
from django.utils import timezone
from django_q.models import Schedule

from apps.nba import services, tasks
from apps.nba.models import NbaApiUsage
from apps.nba.services import DailyApiLimitExceeded, MonthlyReserveReached
from tests.reports_factories import make_season

pytestmark = pytest.mark.django_db

SUMMARY = {"created": 3, "updated": 1, "recovered": 2, "unmatched": 0, "ambiguous": 0}


@pytest.fixture
def api_key(settings):
    settings.RAPID_API_KEY = "test-key"


@pytest.fixture
def in_season():
    season = make_season(
        starts_on=timezone.localdate() - dt.timedelta(days=30),
        ends_on=timezone.localdate() + dt.timedelta(days=120),
    )
    return season


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def sync(**kwargs):
        seen.append(kwargs)
        return SUMMARY

    monkeypatch.setattr(tasks, "sync_injuries", sync)
    return seen


def test_the_schedule_is_there():
    schedule = Schedule.objects.get(name="Daily injury import")
    assert schedule.func == "apps.nba.tasks.scheduled_injury_import"
    assert schedule.cron == "20 12 * * *"
    assert schedule.next_run is not None


def test_it_imports_with_the_superuser_limit(api_key, in_season, calls, settings):
    result = tasks.scheduled_injury_import()

    assert calls == [{"daily_limit": settings.RAPID_API_SUPERUSER_DAILY_LIMIT}]
    assert result == "3 created, 1 updated, 2 recovered, 0 unmatched, 0 ambiguous."


def test_a_manual_refresh_earlier_that_day_does_not_block_it(api_key, settings):
    NbaApiUsage.objects.create(
        provider=services.PROVIDER, usage_date=timezone.localdate(), request_count=1
    )

    with pytest.raises(DailyApiLimitExceeded):
        services._reserve_api_call(daily_limit=settings.RAPID_API_DAILY_LIMIT)
    services._reserve_api_call(daily_limit=settings.RAPID_API_SUPERUSER_DAILY_LIMIT)

    assert NbaApiUsage.objects.get().request_count == 2


def test_the_staff_limit_stays_at_one(settings):
    assert settings.RAPID_API_DAILY_LIMIT == 1


def test_without_a_key_it_skips(settings, in_season, calls):
    settings.RAPID_API_KEY = ""

    assert tasks.scheduled_injury_import() == "skipped: RAPID_API_KEY is not set"
    assert calls == []


def test_without_a_season_it_skips(api_key, calls):
    assert tasks.scheduled_injury_import() == "skipped: no current season is set up"
    assert calls == []


def test_a_week_before_the_season_it_runs_and_before_that_it_skips(api_key, calls):
    today = timezone.localdate()
    season = make_season(
        starts_on=today + dt.timedelta(days=7), ends_on=today + dt.timedelta(days=180)
    )

    assert tasks.why_no_injury_import() is None

    season.starts_on = today + dt.timedelta(days=8)
    season.save()
    assert tasks.why_no_injury_import() == f"the season {season.label} has not started"


def test_after_the_season_it_skips(api_key, calls):
    today = timezone.localdate()
    season = make_season(
        starts_on=today - dt.timedelta(days=200), ends_on=today - dt.timedelta(days=1)
    )

    assert tasks.scheduled_injury_import() == f"skipped: the current season {season.label} is over"
    assert calls == []


@pytest.mark.parametrize(
    "error",
    [DailyApiLimitExceeded("Daily limit reached."), services.NbaApiError("HTTP 503")],
)
def test_a_refused_request_is_reported_not_raised(api_key, in_season, monkeypatch, error):
    def sync(**kwargs):
        raise error

    monkeypatch.setattr(tasks, "sync_injuries", sync)

    assert tasks.scheduled_injury_import() == f"failed: {error}"


def test_a_low_monthly_reserve_is_reported_not_raised(api_key, in_season, monkeypatch):
    error = MonthlyReserveReached.__new__(MonthlyReserveReached)
    RuntimeError.__init__(error, "only 3 requests left")

    def sync(**kwargs):
        raise error

    monkeypatch.setattr(tasks, "sync_injuries", sync)

    assert tasks.scheduled_injury_import() == "failed: only 3 requests left"
