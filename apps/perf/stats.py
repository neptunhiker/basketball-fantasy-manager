"""The figures the performance dashboard shows, worked out in Postgres.

Percentiles rather than averages: one cold request at four seconds drags an
average up for the whole day, while p50 says what a typical page costs and p95
what the slow end of normal looks like. Both come from PERCENTILE_CONT in one
query per section, never from rows pulled into Python.
"""

import datetime as dt
from dataclasses import dataclass

from django.db.models import Aggregate, Avg, Count, F, FloatField, Q
from django.db.models.functions import TruncDay, TruncHour
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import RequestMetric

# p95 thresholds for the status pills, in milliseconds. Fast is what a page
# should feel like; past a second, people notice they are waiting.
FAST_MS = 300
SLOW_MS = 1000

SLOWEST_LIMIT = 20


class Percentile(Aggregate):
    """PERCENTILE_CONT(fraction) WITHIN GROUP (ORDER BY expression).

    `fraction` is written into the SQL, so it only ever comes from this
    module's own constants -- never from a request.
    """

    function = "PERCENTILE_CONT"
    name = "Percentile"
    template = "%(function)s(%(fraction)s) WITHIN GROUP (ORDER BY %(expressions)s)"
    output_field = FloatField()

    def __init__(self, expression, fraction, **extra):
        super().__init__(expression, fraction=float(fraction), **extra)


def figures():
    """The aggregates every section shares, as keyword arguments."""
    return {
        "requests": Count("id"),
        "p50": Percentile("duration_ms", 0.5),
        "p95": Percentile("duration_ms", 0.95),
        "queries": Avg("sql_count"),
        "sql_p95": Percentile("sql_ms", 0.95),
        "bytes_median": Percentile("response_bytes", 0.5),
        "errors": Count("id", filter=Q(status__gte=500)),
    }


@dataclass(frozen=True)
class Period:
    key: str
    label: str
    start: dt.datetime
    end: dt.datetime
    # "hour" or "day": how the trend chart buckets the window.
    bucket: str

    @property
    def previous(self):
        """The window of the same length right before this one.

        Lengths are taken in UTC: two times in the same zone subtract by the
        clock on the wall, which is an hour off across a DST change.
        """
        length = self.end.astimezone(dt.UTC) - self.start.astimezone(dt.UTC)
        start = timezone.localtime(self.start.astimezone(dt.UTC) - length)
        return Period(self.key, self.label, start, self.start, self.bucket)

    def buckets(self):
        """Every bucket start in the window, oldest first, gaps included.

        Hours are UTC hours -- what `trend` groups by -- so the night the
        clocks go back has two 02:00 buckets rather than one holding both,
        and the night they go forward has no 02:00 that cannot exist.
        Days run from local midnight to local midnight, like `TruncDay`.
        """
        moments = []
        if self.bucket == "hour":
            moment = self.start.astimezone(dt.UTC)
            while moment < self.end:
                moments.append(moment)
                moment += dt.timedelta(hours=1)
            return moments
        moment = self.start
        while moment < self.end:
            moments.append(moment)
            next_day = timezone.localdate(moment) + dt.timedelta(days=1)
            moment = timezone.make_aware(dt.datetime.combine(next_day, dt.time.min))
        return moments


PERIODS = {
    "24h": (_("24 hours"), "hour"),
    "7d": (_("7 days"), "day"),
    "30d": (_("30 days"), "day"),
}
DEFAULT_PERIOD = "7d"


def period_for(key, now=None):
    """The window a `?period=` value asks for; the default for anything else.

    Whole buckets: 24 hours start on the hour, 7 and 30 days at local
    midnight, so the headline figures cover exactly what the chart draws.
    """
    if key not in PERIODS:
        key = DEFAULT_PERIOD
    label, bucket = PERIODS[key]
    now = timezone.localtime(now or timezone.now())
    if bucket == "hour":
        # In UTC, for the reason in `Period.buckets`.
        hour = now.astimezone(dt.UTC).replace(minute=0, second=0, microsecond=0)
        start = timezone.localtime(hour - dt.timedelta(hours=23))
    else:
        days = int(key.removesuffix("d"))
        first_day = now.date() - dt.timedelta(days=days - 1)
        start = timezone.make_aware(dt.datetime.combine(first_day, dt.time.min))
    return Period(key, label, start, now, bucket)


def comparable(period):
    """Whether rows reach back to the start of the window before `period`.

    Without that, a comparison would set a whole window against the day or
    two that was recorded: the first weeks after recording began, or once
    the retention has pruned the start away. Pruning goes oldest first, so
    one row at or before the start means the window is there from it on.
    """
    return RequestMetric.objects.filter(recorded_at__lte=period.previous.start).exists()


def window(period, route=None):
    qs = RequestMetric.objects.filter(recorded_at__gte=period.start, recorded_at__lt=period.end)
    if route is not None:
        qs = qs.filter(route=route)
    return qs


def matched(period):
    """The window without requests no URL matched.

    Those are mostly bots trying paths that do not exist: the routes table
    lists them on a line of their own, but they say nothing about how fast
    the app is, so the headline figures and the trend leave them out.
    """
    return window(period).exclude(route="")


def summary(period):
    return matched(period).aggregate(**figures())


def change(current, previous, higher_is_worse=True):
    """How a figure moved against the previous window, or None.

    None without both figures, or from zero -- a percentage of nothing says
    nothing. `tone` is "bad", "good" or "neutral", for the colour.
    """
    if current is None or not previous:
        return None
    percent = (current - previous) / previous * 100
    if round(percent) == 0 or higher_is_worse is None:
        tone = "neutral"
    else:
        tone = "bad" if (percent > 0) == higher_is_worse else "good"
    return {"percent": percent, "up": percent > 0, "flat": round(percent) == 0, "tone": tone}


def status(p95):
    """`fast`, `ok` or `slow` for a p95 in milliseconds; None without one."""
    if p95 is None:
        return None
    if p95 < FAST_MS:
        return "fast"
    if p95 < SLOW_MS:
        return "ok"
    return "slow"


def routes(period):
    """One row per route and request kind (page or HTMX), slowest p95 first."""
    rows = list(
        window(period)
        .values("route", "htmx")
        .annotate(**figures())
        .order_by(F("p95").desc(nulls_last=True), "route", "htmx")
    )
    slowest = max((row["p95"] or 0 for row in rows), default=0)
    for row in rows:
        row["status"] = status(row["p95"])
        # The bar beside p95: share of the slowest route, never quite nothing.
        row["bar_percent"] = max(2, round((row["p95"] or 0) / slowest * 100)) if slowest else 0
    return rows


def route_names(period):
    """Every route with a request in the window, for the trend's picker."""
    names = matched(period).values_list("route", flat=True)
    return list(names.distinct().order_by("route"))


def trend(period, route=None):
    """p50, p95 and request count per bucket, one entry per bucket -- an empty
    bucket comes back with None, so the chart can leave a gap. All routes
    means all matched ones, like `summary`."""
    if period.bucket == "hour":
        bucket = TruncHour("recorded_at", tzinfo=dt.UTC)
    else:
        bucket = TruncDay("recorded_at")
    rows = matched(period) if route is None else window(period, route)
    measured = {
        row["bucket"]: row
        for row in rows.annotate(bucket=bucket)
        .values("bucket")
        .annotate(
            requests=Count("id"),
            p50=Percentile("duration_ms", 0.5),
            p95=Percentile("duration_ms", 0.95),
        )
    }
    empty = {"requests": 0, "p50": None, "p95": None}
    return [{**empty, **measured.get(moment, {}), "bucket": moment} for moment in period.buckets()]


def slowest(period, limit=SLOWEST_LIMIT):
    return list(window(period).order_by("-duration_ms")[:limit])
