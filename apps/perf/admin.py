from django.contrib import admin

from .models import RequestMetric


@admin.register(RequestMetric)
class RequestMetricAdmin(admin.ModelAdmin):
    """Read-only: the rows are measurements, not something to correct."""

    list_display = (
        "recorded_at",
        "method",
        "route",
        "htmx",
        "status",
        "duration_ms",
        "sql_count",
        "sql_ms",
        "response_bytes",
    )
    list_filter = ("route", "htmx", "method", "status")
    search_fields = ("route", "query_keys")
    date_hierarchy = "recorded_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
