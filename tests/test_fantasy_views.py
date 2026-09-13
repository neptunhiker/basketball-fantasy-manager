import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.fantasy import services
from apps.fantasy.models import Roster, Season
from apps.nba.models import Player


@pytest.fixture
def signed_in(client, user, password, db):
    user.is_staff = True
    user.is_superuser = True
    user.save(update_fields=["is_staff", "is_superuser"])
    client.login(email=user.email, password=password)
    return client


def make_season(label, starts_on, ends_on, is_current=False):
    return Season.objects.create(
        label=label, starts_on=starts_on, ends_on=ends_on, is_current=is_current
    )


@pytest.fixture
def seasons(db):
    today = timezone.localdate()
    return {
        "upcoming": make_season(
            "2026-27", today + dt.timedelta(days=40), today + dt.timedelta(days=220), True
        ),
        "running": make_season(
            "2025-26", today - dt.timedelta(days=60), today + dt.timedelta(days=60)
        ),
        "finished": make_season(
            "2024-25", today - dt.timedelta(days=500), today - dt.timedelta(days=320)
        ),
    }


# --- the timing property -----------------------------------------------------


def test_timing_distinguishes_upcoming_running_and_finished(seasons):
    assert seasons["upcoming"].timing == Season.Timing.UPCOMING
    assert seasons["running"].timing == Season.Timing.RUNNING
    assert seasons["finished"].timing == Season.Timing.FINISHED


def test_a_season_can_be_current_before_it_starts(seasons):
    """The real 2026-27 case: flagged current, six weeks from tip-off."""
    upcoming = seasons["upcoming"]
    assert upcoming.is_current
    assert upcoming.timing == Season.Timing.UPCOMING


# --- the view ----------------------------------------------------------------


def test_season_list_requires_login(client, db):
    response = client.get(reverse("fantasy:season-list"))
    assert response.status_code == 302
    assert reverse("accounts:login") in response.url


def test_season_list_shows_every_season_newest_first(signed_in, seasons):
    response = signed_in.get(reverse("fantasy:season-list"))
    assert response.status_code == 200
    labels = [s.label for s in response.context["seasons"]]
    assert labels == ["2026-27", "2025-26", "2024-25"]


def test_counts_are_zero_without_related_rows(signed_in, seasons):
    response = signed_in.get(reverse("fantasy:season-list"))
    for season in response.context["seasons"]:
        assert season.roster_count == 0
        assert season.snapshot_count == 0


def test_counts_do_not_multiply_each_other(signed_in, seasons, manager):
    """Two Count() annotations would cross-join and report 6 and 6 here."""
    season = seasons["running"]
    Roster.objects.create(manager=manager, season=season, name="A")
    Roster.objects.create(manager=manager, season=season, name="B")
    for i in range(3):
        player = Player.objects.create(first_name="P", last_name=str(i), position="G")
        services.record_snapshot(
            player, season, timezone.now() - dt.timedelta(days=i), 1_000_000, "10.00", 1
        )

    response = signed_in.get(reverse("fantasy:season-list"))
    row = next(s for s in response.context["seasons"] if s.pk == season.pk)
    assert row.roster_count == 2
    assert row.snapshot_count == 3


def test_empty_state_explains_why_a_season_is_needed(signed_in):
    body = signed_in.get(reverse("fantasy:season-list")).content.decode()
    assert "No seasons yet" in body


def test_each_timing_renders_its_own_badge(signed_in, seasons):
    """`{% if season.timing == season.Timing.RUNNING %}` would fail silently if
    the nested enum did not resolve in a template, so assert the output."""
    body = signed_in.get(reverse("fantasy:season-list")).content.decode()
    assert "In progress" in body
    assert "Upcoming" in body
    assert "Finished" in body
    assert "badge-success" in body and "badge-pending" in body and "badge-neutral" in body
