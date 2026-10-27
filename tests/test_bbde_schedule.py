"""The daily basketball.de import that Django-Q starts at 12:15."""

import datetime as dt

import pytest
from django.core.management import call_command
from django_q.models import Schedule

from apps.fantasy.models import Season
from apps.nba.bbde import BbdeLoginError
from apps.nba.models import ImportRun
from apps.nba.tasks import scheduled_bbde_import

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
