"""The basketball.de imports Django-Q starts: daily at 12:15, Sundays at 22:15."""

import datetime as dt
from zoneinfo import ZoneInfo

import pytest
from django.core.management import call_command
from django_q.models import Schedule

from apps.fantasy.models import Season
from apps.nba.bbde import BbdeLoginError
from apps.nba.models import ImportRun
from apps.nba.tasks import scheduled_bbde_import, scheduled_salary_import, why_no_salary_import

from .test_bbde_import import FakeClient, pages_of, row


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


@pytest.fixture
def login(settings):
    settings.BBDE_USERNAME = "coach"
    settings.BBDE_PASSWORD = "s3cret"


def test_the_schedule_exists_after_migrate(db):
    schedule = Schedule.objects.get(func="apps.nba.tasks.scheduled_bbde_import")

    assert schedule.schedule_type == Schedule.CRON
    assert schedule.cron == "15 12 * * *"
    assert schedule.repeats == -1
    assert not schedule.args
    assert not schedule.kwargs


def test_without_a_login_the_task_does_nothing(season, settings):
    settings.BBDE_USERNAME = ""
    settings.BBDE_PASSWORD = ""
    client = FakeClient()

    result = scheduled_bbde_import(client=client)

    assert result.startswith("skipped")
    assert client.logins == []
    assert not ImportRun.objects.exists()


def test_the_task_imports_with_the_configured_login(season, teams, login):
    client = FakeClient(pages_of(row("Bam", "Adebayo", bbde_id=1153, position="C")))

    scheduled_bbde_import(client=client)

    assert client.logins == [("coach", "s3cret")]
    run = ImportRun.objects.get()
    assert run.source == ImportRun.Source.SCHEDULED
    assert run.status == ImportRun.Status.SUCCEEDED
    assert run.triggered_by is None


def test_a_rejected_login_fails_the_task_and_the_run(season, teams, login):
    client = FakeClient(login_error=BbdeLoginError("not accepted"))

    with pytest.raises(BbdeLoginError):
        scheduled_bbde_import(client=client)

    assert ImportRun.objects.get().status == ImportRun.Status.FAILED


# -- the Sunday-evening salary import ---------------------------------------------


def test_the_salary_schedule_exists_after_migrate(db):
    schedule = Schedule.objects.get(func="apps.nba.tasks.scheduled_salary_import")

    assert schedule.schedule_type == Schedule.CRON
    assert schedule.cron == "15 22 * * 0"
    assert schedule.repeats == -1
    assert schedule.next_run.weekday() == 6


BERLIN = ZoneInfo("Europe/Berlin")


@pytest.fixture
def trade_market(season):
    season.trade_market_opens_at = dt.datetime(2026, 11, 15, 22, tzinfo=BERLIN)
    season.save()
    return season


@pytest.mark.parametrize(
    "now, runs",
    [
        (dt.datetime(2026, 11, 1, 22, 15, tzinfo=BERLIN), False),
        (dt.datetime(2026, 11, 8, 22, 15, tzinfo=BERLIN), False),
        (dt.datetime(2026, 11, 15, 22, 15, tzinfo=BERLIN), True),
        (dt.datetime(2027, 4, 11, 22, 15, tzinfo=BERLIN), True),
        (dt.datetime(2027, 4, 18, 22, 15, tzinfo=BERLIN), False),
    ],
)
def test_salary_imports_run_from_trade_buying_until_the_season_ends(trade_market, now, runs):
    assert (why_no_salary_import(now) is None) is runs


def test_without_a_trade_buying_date_the_salary_import_skips(season):
    reason = why_no_salary_import(dt.datetime(2026, 11, 15, 22, 15, tzinfo=BERLIN))

    assert "no date for trade buying to open" in reason


def test_without_a_season_the_salary_import_skips(db):
    assert why_no_salary_import() == "no current season is set up"


def test_the_salary_import_skips_before_trade_buying_opens(trade_market, login, monkeypatch):
    monkeypatch.setattr(
        "django.utils.timezone.now", lambda: dt.datetime(2026, 11, 1, 22, 15, tzinfo=BERLIN)
    )
    client = FakeClient()

    assert scheduled_salary_import(client=client).startswith("skipped")
    assert client.logins == []


def test_the_salary_import_imports_once_trade_buying_is_open(
    trade_market, teams, login, monkeypatch
):
    monkeypatch.setattr(
        "django.utils.timezone.now", lambda: dt.datetime(2026, 11, 15, 22, 15, tzinfo=BERLIN)
    )
    client = FakeClient(pages_of(row("Bam", "Adebayo", bbde_id=1153, position="C")))

    scheduled_salary_import(client=client)

    assert ImportRun.objects.get().source == ImportRun.Source.SCHEDULED
