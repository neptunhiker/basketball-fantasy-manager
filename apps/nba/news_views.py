"""The news page: RotoWire's player news, newest first, linked to our players."""

import datetime as dt

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Max, Q
from django.utils import timezone
from django.views.generic import ListView

from apps.fantasy.models import Roster, RosterPlayer, Season

from .models import ROTOWIRE_TEAM_CODES, PlayerNews, Team

# The roster filter's value for "every roster of mine this season".
MY_ROSTERS = "mine"


def current_rosters(user):
    """`user`'s rosters in the current season, the ones the roster filter offers."""
    season = Season.objects.filter(is_current=True).first()
    if season is None:
        return Roster.objects.none()
    return (
        Roster.objects.filter(manager__user=user, season=season)
        .select_related("manager")
        .order_by("name")
    )


def roster_names_by_player(rosters):
    """{player id: [roster name, ...]} for everyone on `rosters` right now."""
    names = {}
    memberships = (
        RosterPlayer.objects.open()
        .filter(roster__in=rosters)
        .order_by("roster__name")
        .values_list("player_id", "roster__name")
    )
    for player_id, roster_name in memberships:
        names.setdefault(player_id, []).append(roster_name)
    return names


def feed_codes_for(abbreviation):
    """Every code the feed may write our team `abbreviation` as."""
    return [abbreviation] + [
        feed for feed, ours in ROTOWIRE_TEAM_CODES.items() if ours == abbreviation
    ]


class NewsListView(LoginRequiredMixin, ListView):
    model = PlayerNews
    template_name = "nba/news_list.html"
    context_object_name = "news"
    paginate_by = 50

    FILTERS = ("kind", "team", "roster")

    def get_queryset(self):
        qs = PlayerNews.objects.select_related("player", "player__team")

        kind = self.request.GET.get("kind", "")
        if kind in PlayerNews.Kind.values:
            qs = qs.filter(kind=kind)

        # The team the item was written under, or the matched player's team
        # now: news from before a trade still belongs to the old team, and a
        # free agent's signing shows up under his new one.
        team = self.request.GET.get("team", "").strip().upper()
        if team:
            qs = qs.filter(
                Q(feed_team_abbreviation__in=feed_codes_for(team))
                | Q(player__team__abbreviation=team)
            )

        roster = self._roster_filter()
        if roster is not None:
            qs = qs.filter(
                player__roster_memberships__roster__in=roster,
                player__roster_memberships__removed_at__isnull=True,
            ).distinct()
        return qs

    def _rosters(self):
        if not hasattr(self, "_current_rosters"):
            self._current_rosters = list(current_rosters(self.request.user))
        return self._current_rosters

    def _roster_filter(self):
        """The rosters the list is narrowed to, or None for everyone's news."""
        value = self.request.GET.get("roster", "").strip()
        if value == MY_ROSTERS:
            return self._rosters()
        chosen = [roster for roster in self._rosters() if str(roster.pk) == value]
        return chosen or None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        context["today"] = today
        context["yesterday"] = today - dt.timedelta(days=1)
        context["kinds"] = PlayerNews.Kind.choices
        context["current_kind"] = self.request.GET.get("kind", "")
        context["teams"] = Team.objects.order_by("name")
        context["rosters"] = self._rosters()
        context["my_rosters"] = MY_ROSTERS
        # Which of your rosters each player is on, for the badge beside him.
        names = roster_names_by_player(self._rosters())
        context["news"] = list(context["news"])
        for item in context["news"]:
            item.on_rosters = names.get(item.player_id, [])
        context["has_filters"] = any(self.request.GET.get(key) for key in self.FILTERS)
        context["active_filter_count"] = sum(
            1 for key in ("team", "roster") if self.request.GET.get(key, "").strip()
        )
        # Every pull rewrites the items it saw, so the newest write is the
        # last pull that brought anything.
        if not self.request.htmx:
            context["last_pull"] = PlayerNews.objects.aggregate(last=Max("updated_at"))["last"]
        return context

    def get_template_names(self):
        if self.request.htmx:
            return ["nba/partials/news_feed.html"]
        return super().get_template_names()
