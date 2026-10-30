from django.contrib import admin

from .models import DreamTeam, DreamTeamPick


class DreamTeamPickInline(admin.TabularInline):
    model = DreamTeamPick
    extra = 0
    fields = ["player", "position", "salary", "total_fp", "games_played"]
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(DreamTeam)
class DreamTeamAdmin(admin.ModelAdmin):
    """Read-only: a Dream Team is calculated, never typed in. To redo one, run
    `manage.py refresh_dream_team`."""

    list_display = ["season", "as_of", "total_fp", "total_salary", "candidates", "updated_at"]
    list_filter = ["season"]
    readonly_fields = [
        "season",
        "as_of",
        "draft_as_of",
        "budget",
        "total_salary",
        "total_fp",
        "candidates",
        "created_at",
        "updated_at",
    ]
    inlines = [DreamTeamPickInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
