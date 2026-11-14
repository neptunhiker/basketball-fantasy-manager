from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.generic import TemplateView

from apps.core.views import StaffRequiredMixin

from . import charts, stats
from .models import RequestMetric

# The request lists under the routes table, as `?list=` values.
LISTS = ("slowest", "latest")


def selection(request):
    """The window, the routes in it, and the route picked from them.

    `?period=` picks the window (24h, 7d, 30d) and `?route=` one route;
    anything unknown in either falls back to the default rather than
    erroring, since both arrive from links and a pasted URL.
    """
    period = stats.period_for(request.GET.get("period"))
    route_names = stats.route_names(period)
    route = request.GET.get("route") or None
    if route not in route_names:
        route = None
    return period, route_names, route


def request_list(request, period, route):
    """The context for the card with the slowest or the latest requests."""
    kind = request.GET.get("list")
    if kind not in LISTS:
        kind = LISTS[0]
    rows = stats.latest(route) if kind == "latest" else stats.slowest(period)
    return {
        "lists": LISTS,
        "request_list": kind,
        "request_rows": rows,
        # The latest are minutes old: "3 minutes ago" says more than a date.
        "relative_times": kind == "latest",
        "as_of": timezone.now(),
        "limit": stats.SLOWEST_LIMIT,
        "per_route": stats.SLOWEST_PER_ROUTE,
        "latest_limit": stats.LATEST_LIMIT,
    }


@method_decorator(never_cache, name="dispatch")
class PerformanceView(StaffRequiredMixin, TemplateView):
    """How fast the app answers, per route, from `RequestMetric` rows.

    `?list=` picks the card at the bottom: the slowest requests in the window
    or the latest ones (see `RequestListView`).
    """

    template_name = "perf/dashboard.html"

    def get_context_data(self, **kwargs):
        period, route_names, route = selection(self.request)
        current = stats.summary(period)
        comparable = stats.comparable(period)
        # Nothing to set against until the window before is there in full.
        previous = stats.summary(period.previous) if comparable else dict.fromkeys(current)
        buckets = stats.trend(period, route)
        return {
            **super().get_context_data(**kwargs),
            **request_list(self.request, period, route),
            "period": period,
            "periods": [(key, label) for key, (label, _bucket) in stats.PERIODS.items()],
            "summary": current,
            "status": stats.status(current["p95"]),
            "changes": {
                "requests": stats.change(
                    current["requests"], previous["requests"], higher_is_worse=None
                ),
                "p50": stats.change(current["p50"], previous["p50"]),
                "p95": stats.change(current["p95"], previous["p95"]),
                "queries": stats.change(current["queries"], previous["queries"]),
                "sql_p95": stats.change(current["sql_p95"], previous["sql_p95"]),
                "bytes_median": stats.change(current["bytes_median"], previous["bytes_median"]),
            },
            "comparable": comparable,
            "routes": stats.routes(period),
            "route_names": route_names,
            "selected_route": route,
            "trend": buckets,
            "trend_charts": charts.trend_charts(buckets),
            "retention_days": settings.PERF_RETENTION_DAYS,
            "has_any_metrics": RequestMetric.objects.exists(),
            "recording": settings.PERF_RECORD_REQUESTS,
            # As a percentage, for the note that counts are of a sample.
            "sample_percent": settings.PERF_SAMPLE_RATE * 100,
            "fast_ms": stats.FAST_MS,
            "slow_ms": stats.SLOW_MS,
            "min_requests": stats.MIN_REQUESTS,
        }


@method_decorator(never_cache, name="dispatch")
class RequestListView(StaffRequiredMixin, TemplateView):
    """Only the request card, for the tabs and the refresh button (HTMX).

    Under /performance/, so it is not measured: refreshing the latest
    requests would otherwise put itself at the top of them. Opened directly,
    it sends you to the same card on the full page.
    """

    template_name = "perf/partials/requests_card.html"

    def get(self, request, *args, **kwargs):
        if not request.htmx:
            query = request.GET.urlencode()
            return redirect(f"{reverse('perf:dashboard')}{'?' + query if query else ''}#requests")
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        period, _route_names, route = selection(self.request)
        return {
            **super().get_context_data(**kwargs),
            **request_list(self.request, period, route),
            "period": period,
            "selected_route": route,
            "fast_ms": stats.FAST_MS,
            "slow_ms": stats.SLOW_MS,
        }
