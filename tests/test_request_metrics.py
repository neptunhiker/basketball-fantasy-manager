"""What `apps.perf` records about a request, and what it leaves alone."""

import datetime as dt
import importlib
from unittest import mock

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.perf.middleware import parameter_names
from apps.perf.models import RequestMetric
from apps.perf.tasks import prune_request_metrics


@pytest.fixture
def recording(settings):
    settings.PERF_RECORD_REQUESTS = True
    settings.PERF_SAMPLE_RATE = 1.0
    return settings


@pytest.fixture
def signed_in(client, user, password):
    client.login(email=user.email, password=password)
    return client


def test_a_request_is_recorded_by_its_route(recording, signed_in):
    response = signed_in.get(reverse("nba:player-list"), {"sort": "salary", "position": "G"})

    metric = RequestMetric.objects.get()
    assert metric.route == "nba:player-list"
    assert metric.method == "GET"
    assert metric.status == 200
    assert metric.htmx is False
    assert metric.duration_ms > 0
    assert metric.sql_count > 0
    assert metric.sql_ms >= 0
    assert metric.response_bytes == len(response.content)


def test_only_parameter_names_are_kept_never_their_values(recording, signed_in):
    signed_in.get(reverse("nba:player-list"), {"q": "james", "team": "LAL"})

    assert RequestMetric.objects.get().query_keys == "q,team"


def test_an_htmx_request_is_marked_as_one(recording, signed_in):
    signed_in.get(reverse("nba:player-list"), HTTP_HX_REQUEST="true")

    assert RequestMetric.objects.get().htmx is True


def test_the_query_count_matches_what_the_request_ran(recording, signed_in):
    """Counted through an execute wrapper, so DEBUG does not have to be on --
    and the metric's own insert is not part of the count."""
    with CaptureQueriesContext(connection) as captured:
        signed_in.get(reverse("nba:player-list"))

    metric = RequestMetric.objects.get()
    assert metric.sql_count == len(captured.captured_queries) - 1


def test_staff_see_what_a_response_cost(client, staff_user, password):
    """The header comes with recording off too: it costs nothing to send."""
    client.login(email=staff_user.email, password=password)

    response = client.get(reverse("nba:player-list"))

    timing = response["Server-Timing"]
    assert timing.startswith("app;dur=")
    assert "db;dur=" in timing
    assert 'queries"' in timing
    assert not RequestMetric.objects.exists()


def test_nobody_else_gets_the_timings(signed_in, client):
    """An exact server time would let anyone tell from the password reset,
    which only mails accounts that exist, whether an address has one."""
    assert "Server-Timing" not in signed_in.get(reverse("nba:player-list"))

    signed_in.logout()
    assert "Server-Timing" not in client.get(reverse("accounts:login"))


def test_a_bare_query_string_is_a_value_not_a_name(recording, signed_in):
    signed_in.get(reverse("nba:player-list") + "?someone@example.com&sort=avg")

    assert RequestMetric.objects.get().query_keys == "sort"


def test_a_redirect_is_recorded_too(recording, client, db):
    """Signed out, the list sends you to the login page: still a request."""
    client.get(reverse("nba:player-list"))

    metric = RequestMetric.objects.get()
    assert metric.route == "nba:player-list"
    assert metric.status == 302


def test_a_path_nothing_matches_is_recorded_without_a_route(recording, client, db):
    client.get("/no-such-page/")

    metric = RequestMetric.objects.get()
    assert metric.route == ""
    assert metric.status == 404


def test_the_health_probe_is_not_measured(recording, client, db):
    response = client.get(reverse("healthz"))

    assert "Server-Timing" not in response
    assert not RequestMetric.objects.exists()


def test_a_sample_rate_of_zero_records_nothing(recording, signed_in):
    recording.PERF_SAMPLE_RATE = 0.0

    response = signed_in.get(reverse("nba:player-list"))

    assert response.status_code == 200
    assert not RequestMetric.objects.exists()


def test_a_failed_write_still_serves_the_page(recording, signed_in):
    with mock.patch.object(RequestMetric.objects, "create", side_effect=RuntimeError("down")):
        response = signed_in.get(reverse("nba:player-list"))

    assert response.status_code == 200


def make_metric(recorded_at):
    return RequestMetric.objects.create(
        recorded_at=recorded_at,
        route="nba:player-list",
        method="GET",
        status=200,
        duration_ms=10,
        sql_count=3,
        sql_ms=2,
    )


def test_pruning_keeps_the_retention_window(settings, db):
    settings.PERF_RETENTION_DAYS = 30
    now = timezone.now()
    kept = make_metric(now - dt.timedelta(days=29))
    make_metric(now - dt.timedelta(days=31))

    assert prune_request_metrics(now=now) == 1
    assert list(RequestMetric.objects.all()) == [kept]


@pytest.mark.django_db
def test_the_prune_schedule_exists():
    from django_q.models import Schedule

    schedule = Schedule.objects.get(name="Prune request metrics")
    assert schedule.func == "apps.perf.tasks.prune_request_metrics"
    assert schedule.cron == "30 4 * * *"


def test_the_dashboard_does_not_measure_itself(recording, client, staff_user, password):
    """Its percentile queries would land in the figures it shows."""
    client.login(email=staff_user.email, password=password)

    assert client.get(reverse("perf:dashboard")).status_code == 200
    assert not RequestMetric.objects.exists()


def test_the_dashboard_rows_recorded_before_are_dropped(db):
    from django.apps import apps

    migration = importlib.import_module("apps.perf.migrations.0003_drop_dashboard_metrics")
    for route in ("perf:dashboard", "nba:player-list"):
        RequestMetric.objects.create(
            recorded_at=timezone.now(),
            route=route,
            method="GET",
            status=200,
            duration_ms=100,
            sql_count=1,
            sql_ms=1,
        )

    migration.drop_dashboard_metrics(apps, None)

    assert list(RequestMetric.objects.values_list("route", flat=True)) == ["nba:player-list"]


@pytest.mark.parametrize(
    ("query_string", "expected"),
    [
        ("a1b2c3d4", ""),
        ("q=", "q"),
        ("q=x&token", "q"),
        ("sort=avg&q=x&sort=pts", "q,sort"),
        ("%5Bf%5D=1", "[f]"),
        ("someone%40example.com=1", ""),
        ("", ""),
    ],
)
def test_only_parts_with_an_equals_sign_name_a_parameter(query_string, expected):
    """A bare part is a value sent on its own -- an invite code, say -- even
    when it looks like a name."""
    assert parameter_names(query_string) == expected


def test_a_bare_token_is_not_recorded(recording, signed_in):
    signed_in.get(reverse("nba:player-list") + "?a1b2c3d4e5f6&sort=avg")

    assert RequestMetric.objects.get().query_keys == "sort"
