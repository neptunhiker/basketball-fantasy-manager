from django.contrib import admin

from .models import RecommendedTrade, Report, ReportRun, ReportSettings


@admin.register(ReportSettings)
class ReportSettingsAdmin(admin.ModelAdmin):
    list_display = ["__str__", "default_model", "default_effort", "sunday_run_enabled"]

    def has_add_permission(self, request):
        return not ReportSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


class ReportInline(admin.TabularInline):
    model = Report
    extra = 0
    fields = ["kind", "roster", "status", "attempts", "output_tokens", "cost_usd", "error"]
    readonly_fields = fields
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(ReportRun)
class ReportRunAdmin(admin.ModelAdmin):
    """Runs are made by the app (Reports page, `manage.py run_report`, the
    Sunday schedule), never typed in. Only publishing is changed here."""

    list_display = [
        "created_at",
        "season",
        "model",
        "effort",
        "trigger",
        "scope",
        "status",
        "is_published",
    ]
    list_filter = ["season", "status", "is_published", "model", "trigger"]
    fields = [
        "season",
        "trigger",
        "started_by",
        "source_run",
        "model",
        "effort",
        "label",
        "prompt_version",
        "scope",
        "data_as_of",
        "status",
        "finished_at",
        "is_published",
        "published_at",
        "published_by",
        "player_refs",
        "shared_input",
    ]
    readonly_fields = [field for field in fields if field not in ("label", "is_published")]
    inlines = [ReportInline]

    def has_add_permission(self, request):
        return False


class RecommendedTradeInline(admin.TabularInline):
    model = RecommendedTrade
    extra = 0
    fields = ["rank", "trade_ref", "player_out", "player_in", "confidence", "reasoning"]
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = ["run", "kind", "roster", "status", "cost_usd", "finished_at"]
    list_filter = ["kind", "status"]
    inlines = [RecommendedTradeInline]

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        return False
