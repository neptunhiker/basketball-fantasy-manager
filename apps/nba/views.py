from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.views.generic import ListView

from .models import Player, Team


class TeamListView(LoginRequiredMixin, ListView):
    """All 30 franchises, grouped the way the standings are."""

    model = Team
    template_name = "nba/team_list.html"
    context_object_name = "teams"

    def get_queryset(self):
        # The stored codes happen to sort into the conventional order
        # (East before West, Atlantic/Central/Southeast, Northwest/Pacific/
        # Southwest), so plain alphabetical ordering is correct here. Rename a
        # choice value and this quietly stops being true.
        return Team.objects.order_by("conference", "division", "name")


class PlayerListView(LoginRequiredMixin, ListView):
    model = Player
    template_name = "nba/player_list.html"
    context_object_name = "players"
    paginate_by = 50

    def get_queryset(self):
        qs = Player.objects.select_related("team")

        # Retired and released players stay in the table so their history keeps
        # resolving, but they are noise on a roster screen unless asked for.
        if not self.request.GET.get("inactive"):
            qs = qs.filter(is_active=True)

        # Each term has to match somewhere, so "james l" finds LeBron James
        # while a single "james" still matches the surname on its own.
        for term in self.request.GET.get("q", "").split():
            qs = qs.filter(Q(first_name__icontains=term) | Q(last_name__icontains=term))

        if (position := self.request.GET.get("position")) in Player.Position.values:
            qs = qs.filter(position=position)

        if team := self.request.GET.get("team", "").strip():
            qs = qs.filter(team__abbreviation=team)

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["positions"] = Player.Position.choices
        context["teams"] = Team.objects.order_by("name")
        return context

    def get_template_names(self):
        # HTMX filter requests only need the table swapped in.
        if self.request.htmx:
            return ["nba/partials/player_table.html"]
        return super().get_template_names()
