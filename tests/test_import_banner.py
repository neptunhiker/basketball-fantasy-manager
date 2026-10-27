"""The banner every page shows while a basketball.de import is running."""

from datetime import timedelta

import pytest
from django.urls import resolve, reverse
from django.utils import timezone

from apps.nba.models import ImportRun

BANNER = "Prices are being updated from basketball.de"


@pytest.fixture
def signed_in(client, user, password):
    client.login(email=user.email, password=password)
    return client


def dashboard(client):
    return client.get(reverse("core:dashboard")).content.decode()


def test_a_running_import_shows_the_banner(signed_in):
    ImportRun.objects.create(source=ImportRun.Source.SCHEDULED)

    assert BANNER in dashboard(signed_in)


def test_no_banner_without_a_running_import(signed_in):
    ImportRun.objects.create(source=ImportRun.Source.WEB, status=ImportRun.Status.SUCCEEDED)
    ImportRun.objects.create(source=ImportRun.Source.WEB, status=ImportRun.Status.FAILED)

    assert BANNER not in dashboard(signed_in)


def test_a_dry_run_shows_no_banner(signed_in):
    ImportRun.objects.create(source=ImportRun.Source.COMMAND, dry_run=True)

    assert BANNER not in dashboard(signed_in)


def test_a_run_that_died_long_ago_shows_no_banner(signed_in):
    ImportRun.objects.create(
        source=ImportRun.Source.WEB, started_at=timezone.now() - timedelta(hours=1)
    )

    assert BANNER not in dashboard(signed_in)


def test_the_manual_import_runs_outside_the_request_transaction():
    # Inside it, the "running" row would stay invisible to everyone else
    # until the import was over, and the banner would never appear.
    view = resolve(reverse("nba:bbde-import")).func

    assert "default" in getattr(view, "_non_atomic_requests", set())
