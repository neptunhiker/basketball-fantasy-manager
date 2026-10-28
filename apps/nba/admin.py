from django.contrib import admin

from .models import (
    ApiQuota,
    ImportRun,
    Player,
    PlayerInjury,
    PlayerNews,
    PlayerSeasonStats,
    SeasonStatsImport,
    Team,
)


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ["name", "abbreviation", "conference", "division"]
    list_filter = ["conference", "division"]
    search_fields = ["name", "abbreviation"]


@admin.register(Player)
class PlayerAdmin(admin.ModelAdmin):
    list_display = [
        "full_name",
        "position",
        "team",
        "is_active",
        "is_rookie",
        "bbde_id",
        "rotowire_id",
    ]
    list_filter = ["position", "is_active", "is_rookie", "team"]
    search_fields = ["first_name", "last_name"]
    list_select_related = ["team"]
    autocomplete_fields = ["team"]


@admin.register(PlayerInjury)
class PlayerInjuryAdmin(admin.ModelAdmin):
    list_display = ["player", "status", "injury_type", "observed_at", "reported_at", "provider"]
    list_filter = ["status", "provider", "observed_at"]
    search_fields = [
        "player__first_name",
        "player__last_name",
        "feed_player_name",
        "injury_type",
        "short_comment",
        "long_comment",
    ]
    list_select_related = ["player"]
    autocomplete_fields = ["player"]
    date_hierarchy = "observed_at"
    ordering = ["-observed_at", "-created_at"]


class MatchedFilter(admin.SimpleListFilter):
    title = "matched"
    parameter_name = "matched"

    def lookups(self, request, model_admin):
        return [("yes", "Linked to a player"), ("no", "Not linked")]

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(player__isnull=False)
        if self.value() == "no":
            return queryset.filter(player__isnull=True)
        return queryset


@admin.register(PlayerNews)
class PlayerNewsAdmin(admin.ModelAdmin):
    """The RotoWire news log. Only the player link is meant to be edited: set it
    by hand for an item the import could not place."""

    list_display = [
        "published_on",
        "feed_player_name",
        "feed_team_abbreviation",
        "kind",
        "headline",
        "player",
    ]
    list_filter = [MatchedFilter, "kind", "feed_team_abbreviation"]
    search_fields = ["feed_player_name", "headline", "body"]
    list_select_related = ["player"]
    autocomplete_fields = ["player"]
    date_hierarchy = "published_on"
    readonly_fields = [
        "rotowire_news_id",
        "kind",
        "headline",
        "body",
        "url",
        "published_on",
        "first_seen_at",
        "feed_player_id",
        "feed_player_name",
        "feed_team_abbreviation",
        "feed_position",
        "raw_payload",
    ]

    def has_add_permission(self, request):
        return False


@admin.register(ImportRun)
class ImportRunAdmin(admin.ModelAdmin):
    """The import log. Read-only: a run is a record of what happened."""

    list_display = [
        "started_at",
        "status",
        "source",
        "dry_run",
        "triggered_by",
        "rows",
        "matched",
        "created",
        "inactivated",
    ]
    list_filter = ["status", "source", "dry_run"]
    list_select_related = ["triggered_by"]
    date_hierarchy = "started_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(SeasonStatsImport)
class SeasonStatsImportAdmin(admin.ModelAdmin):
    """The season stats upload log. Read-only, like the basketball.de log."""

    list_display = [
        "created_at",
        "season",
        "status",
        "source",
        "triggered_by",
        "rows",
        "matched",
        "created",
        "updated",
        "removed",
    ]
    list_filter = ["status", "season", "source"]
    list_select_related = ["triggered_by"]
    date_hierarchy = "created_at"
    # The pending rows are hundreds of lines of numbers, and gone after import.
    exclude = ["payload"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(PlayerSeasonStats)
class PlayerSeasonStatsAdmin(admin.ModelAdmin):
    """Read-only: a wrong number is fixed in the file and uploaded again."""

    list_display = [
        "player",
        "season",
        "team_abbreviation",
        "games_played",
        "minutes",
        "points",
        "rebounds",
        "assists",
        "game_fantasy_points",
    ]
    list_filter = ["season"]
    search_fields = ["player__first_name", "player__last_name", "source_name"]
    list_select_related = ["player"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ApiQuota)
class ApiQuotaAdmin(admin.ModelAdmin):
    """Read-only: each request rewrites the row from RapidAPI's own figures."""

    list_display = ["provider", "remaining", "limit", "resets_at", "reported_at"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
