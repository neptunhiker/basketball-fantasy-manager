from django.conf import settings
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.generic import TemplateView

from apps.core.views import StaffRequiredMixin

from . import charts, stats
from .models import RequestMetric


@method_decorator(never_cache, name="dispatch")
class PerformanceView(StaffRequiredMixin, TemplateView):
    """How fast the app answers, per route, from `RequestMetric` rows.

    `?period=` picks the window (24h, 7d, 30d) and `?route=` narrows the trend
    chart to one route; anything unknown in either falls back to the default
    rather than erroring, since both arrive from links and a pasted URL.
    """

    template_name = "perf/dashboard.html"

    def get_context_data(self, **kwargs):
        period = stats.period_for(self.request.GET.get("period"))
        current = stats.summary(period)
        comparable = stats.comparable(period)
        # Nothing to set against until the window before is there in full.
        previous = stats.summary(period.previous) if comparable else dict.fromkeys(current)
        route_names = stats.route_names(period)
        route = self.request.GET.get("route") or None
        if route not in route_names:
            route = None
        buckets = stats.trend(period, route)
        return {
            **super().get_context_data(**kwargs),
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
            "slowest": stats.slowest(period),
            "limit": stats.SLOWEST_LIMIT,
            "retention_days": settings.PERF_RETENTION_DAYS,
            "has_any_metrics": RequestMetric.objects.exists(),
            "recording": settings.PERF_RECORD_REQUESTS,
            "fast_ms": stats.FAST_MS,
            "slow_ms": stats.SLOW_MS,
        }
