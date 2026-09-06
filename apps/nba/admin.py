from django.contrib import admin

from .models import Player, Team


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
