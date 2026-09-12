from django.contrib import admin

from .models import Player, PlayerInjury, Team


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ["name", "abbreviation", "conference", "division"]
    list_filter = ["conference", "division"]
    search_fields = ["name", "abbreviation"]


@admin.register(Player)
class PlayerAdmin(admin.ModelAdmin):
    list_display = ["full_name", "position", "team", "is_active"]
    list_filter = ["position", "is_active", "team"]
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
