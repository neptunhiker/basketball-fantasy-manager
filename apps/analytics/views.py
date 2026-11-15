import uuid
from decimal import Decimal

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Prefetch
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.generic import TemplateView

from apps.fantasy.models import Season
from apps.nba.charts import scatter_chart
from apps.nba.models import Player

from . import charts
from .dream_team import changes, missing_reason
from .insights import points_race
from .models import DreamTeam, DreamTeamPick

MILLION = Decimal(1_000_000)
# How many of the best players the Dream Team could not fit in are named.
LEFT_OUT = 3
# The chart's colours: the brand violet for the fifteen, quiet grey for the rest.
PICK_COLOR = "#7c5ef3"
OTHER_COLOR = "#a1a1aa"
# The chart's drawing sizes, wide from `md` up and compact below it.
WIDE_CHART = {"width": 920, "height": 340}
COMPACT_CHART = {"width": 360, "height": 300}


class AnalyticsMixin(LoginRequiredMixin):
    """What every Analytics tab shares: the current season and which tab is open.

    The tabs are separate pages rather than panels of one, so each pays only
    for its own work and has a URL of its own (`templates/analytics/base.html`
    draws the tab bar). Without a current season the base template says so and
    the tab's own content is not rendered, so `season` is None is all a tab
    has to handle.
    """

    tab = None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["season"] = Season.objects.filter(is_current=True).first()
        context["tab"] = self.tab
        return context


class DreamTeamView(AnalyticsMixin, TemplateView):
    """The Dream Team tab. Reads stored results only: the solving happens
    after imports (`dream_team.refresh`), never on a page view."""

    template_name = "analytics/dream_team.html"
    tab = "dream-team"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        season = context["season"]
        if season is None:
            return context

        dream_team = (
            DreamTeam.objects.filter(season=season)
            .prefetch_related(
                Prefetch("picks", queryset=DreamTeamPick.objects.select_related("player__team"))
            )
            .first()
        )
        if dream_team is None:
            context["missing"] = missing_reason(season)
            return context

        result = changes(dream_team)
        picks = list(dream_team.picks.all())
        context.update(
            dream_team=dream_team,
            changes=result,
            groups=_groups(picks, {pick.player_id for pick in result.came_in}),
            games=sum(pick.games_played for pick in picks),
            # Share of the budget spent, for the bar under "Salary used".
            budget_used=round(dream_team.total_salary / dream_team.budget * 100)
            if dream_team.budget
            else 0,
            **_pool(dream_team, picks),
        )
        return context


class InsightsView(AnalyticsMixin, TemplateView):
    """The Insights tab: every active roster's points race against the Dream Team.

    Sums stored snapshots and results on read (`insights.points_race`); a
    handful of queries however many rosters race.
    """

    template_name = "analytics/insights.html"
    tab = "insights"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        season = context["season"]
        if season is None:
            return context
        race = points_race(season, viewer=self.request.user)
        context["race"] = race
        if race is None:
            return context
        rows = [
            {
                "rank": rank,
                "line": line,
                "gap": gap,
                # Shown as a size and a direction, so the template never has
                # to print a minus sign for "ahead".
                "gap_size": abs(gap) if gap is not None else None,
                "ahead": gap is not None and gap < 0,
            }
            for rank, line, gap in race.standings
        ]
        own = [row for row in rows if row["line"].is_own]
        context.update(
            # Three drawings of the same race, each close to the size it is
            # shown at: desktop, tablet, and a phone's that leaves the naming
            # of the lines to the legend.
            race_chart=charts.race_chart(race, **charts.WIDE),
            race_medium_chart=charts.race_chart(race, **charts.MEDIUM),
            race_compact_chart=charts.race_chart(race, **charts.COMPACT),
            race_readout=charts.race_readout(race),
            standings=rows,
            leader=next((row for row in rows if not row["line"].is_dream_team), None),
            own_best=own[0] if own else None,
            roster_count=len(race.rosters),
        )
        return context


def _groups(picks, new_ids):
    """The picks by position, Guards first, best scorer first within each.

    Every row carries `is_new` (came in with this import) and `bar`, its
    points as a share of the best pick's, for the small bar beside them.
    """
    best = max((pick.total_fp for pick in picks), default=0) or 1
    labels = {
        Player.Position.GUARD: _("Guards"),
        Player.Position.FORWARD: _("Forwards"),
        Player.Position.CENTER: _("Centers"),
    }
    groups = []
    for position, label in labels.items():
        rows = sorted(
            (pick for pick in picks if pick.position == position),
            key=lambda pick: (-pick.total_fp, pick.player.last_name),
        )
        if not rows:
            continue
        for pick in rows:
            pick.is_new = pick.player_id in new_ids
            pick.bar = max(0, round(pick.total_fp / best * 100))
        groups.append(
            {
                "position": position,
                "label": label,
                "rows": rows,
                "salary": sum(pick.salary for pick in rows),
                "points": sum(pick.total_fp for pick in rows),
            }
        )
    return groups


def _pool(dream_team, picks):
    """The chart and the best players left out, from the stored pool.

    One query for the players' names and teams; everything else is in the
    stored result.
    """
    picked = {str(pick.player_id) for pick in picks}
    players = Player.objects.select_related("team").in_bulk(
        [entry["player"] for entry in dream_team.pool]
    )
    entries = []
    for entry in dream_team.pool:
        player = players.get(uuid.UUID(entry["player"]))
        if player is None:
            continue
        entries.append(
            {
                "player": player,
                "position": entry["position"],
                "salary": Decimal(entry["salary"]),
                "points": Decimal(entry["points"]),
                "picked": entry["player"] in picked,
            }
        )

    # Picks last, so SVG paints them over the grey crowd.
    entries.sort(key=lambda entry: entry["picked"])
    points = [
        (
            f"{entry['player'].first_name} {entry['player'].last_name}".strip(),
            entry["salary"] / MILLION,
            entry["points"],
            None,
            {
                "color": PICK_COLOR if entry["picked"] else OTHER_COLOR,
                "picked": entry["picked"],
                "meta": " · ".join(
                    part
                    for part in (
                        entry["player"].team.abbreviation if entry["player"].team else "",
                        entry["position"],
                    )
                    if part
                ),
                "detail_url": reverse("nba:player-detail", args=[entry["player"].slug]),
            },
        )
        for entry in entries
    ]
    # Only those who outscored the weakest pick: anyone below him is not a
    # player the Dream Team "left out", just one it never needed.
    weakest = min((pick.total_fp for pick in picks), default=0)
    left_out = sorted(
        (entry for entry in entries if not entry["picked"] and entry["points"] > weakest),
        key=lambda entry: (-entry["points"], entry["salary"]),
    )[:LEFT_OUT]
    return {
        # Two drawings of the same dots: a wide one, and a taller, narrower one
        # for phones, where the wide one would shrink its labels to nothing.
        "chart": scatter_chart(points, **WIDE_CHART),
        "compact_chart": scatter_chart(points, x_ticks=4, y_ticks=4, **COMPACT_CHART),
        "left_out": left_out,
        "pick_color": PICK_COLOR,
    }
