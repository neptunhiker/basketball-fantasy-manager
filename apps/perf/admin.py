from django.contrib import admin

from .models import Deploy, RequestMetric


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


@admin.register(Deploy)
class DeployAdmin(admin.ModelAdmin):
    """Read-only like the metrics: written by `record_deploy`, not by hand.

    Deletable, though -- a deploy recorded by mistake can go.
    """

    list_display = ("deployed_at", "short_sha", "subject")
    search_fields = ("sha", "subject")
    date_hierarchy = "deployed_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
