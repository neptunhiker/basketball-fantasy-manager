"""The staff performance dashboard: who may see it, and whether its figures add up."""

import datetime as dt

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.perf import charts, stats
from apps.perf.models import RequestMetric
from apps.perf.templatetags.perf import DASH, MINUS, duration, signed_percent

URL = reverse("perf:dashboard")


@pytest.fixture
def staff_client(client, staff_user, password):
    client.login(email=staff_user.email, password=password)
    return client


def metric(ms, route="nba:player-list", at=None, htmx=False, status=200, **extra):
    return RequestMetric.objects.create(
        recorded_at=at or timezone.now() - dt.timedelta(seconds=1),
        route=route,
        method="GET",
        status=status,
        htmx=htmx,
        duration_ms=ms,
        sql_count=extra.pop("sql_count", 10),
        sql_ms=extra.pop("sql_ms", ms / 4),
        response_bytes=extra.pop("response_bytes", 1000),
        **extra,
    )


# --- access ------------------------------------------------------------------


def test_signed_out_goes_to_the_login_page(client, db):
    response = client.get(URL)

    assert response.status_code == 302
    assert reverse("accounts:login") in response["Location"]


def test_a_member_who_is_not_staff_is_refused(client, user, password):
    client.login(email=user.email, password=password)

    assert client.get(URL).status_code == 403


def test_staff_see_the_empty_state_before_anything_is_recorded(staff_client):
    response = staff_client.get(URL)

    assert response.status_code == 200
    assert "No requests recorded yet" in response.content.decode()


def test_the_sidebar_links_the_dashboard_for_staff_only(client, user, staff_client, password):
    assert URL in staff_client.get(reverse("core:dashboard")).content.decode()

    client.logout()
    client.login(email=user.email, password=password)
    assert URL not in client.get(reverse("core:dashboard")).content.decode()


def test_the_page_is_never_cached(staff_client):
    assert "no-cache" in staff_client.get(URL)["Cache-Control"]


# --- the figures ---------------------------------------------------------------


def test_percentiles_come_from_postgres_like_the_textbook_says(db):
    """PERCENTILE_CONT interpolates: of 10, 20, ..., 100 the median is 55 and
    p95 is 95.5."""
    for ms in range(10, 101, 10):
        metric(ms)

    summary = stats.summary(stats.period_for("24h"))

    assert summary["requests"] == 10
    assert summary["p50"] == pytest.approx(55)
    assert summary["p95"] == pytest.approx(95.5)
    assert summary["queries"] == pytest.approx(10)


def test_requests_outside_the_window_do_not_count(db):
    metric(100)
    metric(9000, at=timezone.now() - dt.timedelta(days=2))

    assert stats.summary(stats.period_for("24h"))["requests"] == 1
    assert stats.summary(stats.period_for("7d"))["requests"] == 2


def test_server_errors_are_counted(db):
    metric(100)
    metric(100, status=500)
    metric(100, status=404)

    assert stats.summary(stats.period_for("24h"))["errors"] == 1


def test_routes_are_split_into_pages_and_htmx_and_slowest_comes_first(db):
    metric(100, route="core:dashboard")
    metric(900, route="nba:player-list")
    metric(400, route="nba:player-list", htmx=True)

    rows = stats.routes(stats.period_for("24h"))

    assert [(row["route"], row["htmx"]) for row in rows] == [
        ("nba:player-list", False),
        ("nba:player-list", True),
        ("core:dashboard", False),
    ]
    assert [row["status"] for row in rows] == ["ok", "ok", "fast"]
    assert rows[0]["bar_percent"] == 100


@pytest.mark.parametrize(
    ("p95", "expected"), [(None, None), (120, "fast"), (300, "ok"), (999, "ok"), (1000, "slow")]
)
def test_status_thresholds(p95, expected):
    assert stats.status(p95) == expected


def test_a_change_knows_whether_it_is_good_news():
    slower = stats.change(120, 100)
    faster = stats.change(80, 100)
    busier = stats.change(120, 100, higher_is_worse=None)

    assert (slower["tone"], slower["up"]) == ("bad", True)
    assert (faster["tone"], faster["up"]) == ("good", False)
    assert busier["tone"] == "neutral"
    assert stats.change(100, 100)["flat"] is True
    assert stats.change(100, None) is None
    assert stats.change(100, 0) is None


def test_the_previous_window_is_the_same_length_right_before():
    period = stats.period_for("7d")

    assert period.previous.end == period.start
    assert period.previous.end - period.previous.start == period.end - period.start


# --- the trend -------------------------------------------------------------------


@pytest.mark.parametrize(("key", "count"), [("24h", 24), ("7d", 7), ("30d", 30)])
def test_the_trend_has_one_bucket_per_hour_or_day(db, key, count):
    assert len(stats.trend(stats.period_for(key))) == count


def test_an_empty_bucket_stays_empty_and_breaks_the_line(db):
    now = timezone.now()
    metric(200, at=now - dt.timedelta(days=4))
    metric(300, at=now - dt.timedelta(seconds=1))

    buckets = stats.trend(stats.period_for("7d"))
    chart = charts.trend_chart(buckets, **charts.WIDE)

    assert [bucket["requests"] for bucket in buckets] == [0, 0, 1, 0, 0, 0, 1]
    assert buckets[3]["p95"] is None
    # Two separate dots, never a line drawn across the quiet days.
    assert chart["p95_path"].count("M") == 2
    assert "L" not in chart["p95_path"]
    assert sum(point["alone95"] for point in chart["points"]) == 2


def test_the_trend_can_follow_one_route(db):
    metric(100, route="core:dashboard")
    metric(900, route="nba:player-list")

    buckets = stats.trend(stats.period_for("24h"), route="core:dashboard")

    assert sum(bucket["requests"] for bucket in buckets) == 1


def test_no_chart_without_a_single_measurement(db):
    assert charts.trend_chart(stats.trend(stats.period_for("7d")), **charts.WIDE) is None


def test_the_axis_starts_at_zero_and_ends_on_a_round_number():
    assert charts.nice_top(1680) == (2000, 500)
    assert charts.nice_top(73) == (80, 20)


# --- the page ----------------------------------------------------------------------


def test_the_page_shows_routes_and_the_slowest_request(staff_client):
    metric(4321, route="nba:player-list", query_keys="position,sort")
    metric(80, route="core:dashboard")

    content = staff_client.get(URL, {"period": "24h"}).content.decode()

    assert "nba:player-list" in content
    assert "core:dashboard" in content
    assert "4.32 s" in content
    assert "position" in content


def test_an_unknown_period_or_route_falls_back_instead_of_failing(staff_client):
    metric(100)

    response = staff_client.get(URL, {"period": "1y", "route": "no:such-route"})

    assert response.status_code == 200
    assert response.context["period"].key == stats.DEFAULT_PERIOD
    assert response.context["selected_route"] is None


def test_a_known_route_narrows_the_trend(staff_client):
    metric(100, route="core:dashboard")

    response = staff_client.get(URL, {"route": "core:dashboard"})

    assert response.context["selected_route"] == "core:dashboard"


def test_the_page_costs_the_same_however_many_requests_are_stored(staff_client):
    metric(100)
    with CaptureQueriesContext(connection) as small:
        staff_client.get(URL)

    for index in range(40):
        metric(100 + index, route=f"route:{index % 8}", htmx=index % 2 == 0)
    with CaptureQueriesContext(connection) as large:
        staff_client.get(URL)

    assert len(large) == len(small)


# --- formatting ---------------------------------------------------------------------


def test_durations_read_as_milliseconds_or_seconds():
    assert duration(None) == DASH
    assert duration(812.4) == "812 ms"
    assert duration(1530) == "1.53 s"


def test_a_flat_change_has_no_sign():
    assert signed_percent(stats.change(100, 100)) == "±0%"
    assert signed_percent(stats.change(112, 100)) == "+12%"
    assert signed_percent(stats.change(88, 100)) == f"{MINUS}12%"
