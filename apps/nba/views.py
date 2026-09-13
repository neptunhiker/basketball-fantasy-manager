from decimal import Decimal, InvalidOperation

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import (
    Case,
    DecimalField,
    Exists,
    ExpressionWrapper,
    F,
    OuterRef,
    Prefetch,
    Q,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import Greatest
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import formats, timezone
from django.views import View
from django.views.generic import DetailView, ListView

from apps.fantasy import services
from apps.fantasy.models import PlayerSnapshot, Roster, WatchlistEntry

from . import charts, stats
from .models import Player, PlayerInjury, Team


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

    # Maps the column name a header link sends to the field(s) it sorts on.
    # Name and team are strings, so they never need a nulls_last() default;
    # the numeric columns do, so unpriced/unscored players fall to the bottom
    # instead of jumping to the top of an ascending sort.
    SORT_FIELDS = {
        "name": ["last_name", "first_name"],
        "position": ["position"],
        "team": ["team__name"],
        "salary": [F("current_salary")],
        "expected": [F("predicted_salary_amount")],
        "difference": [F("salary_difference_amount")],
        "points": [F("current_total_fp")],
        "avg": [F("current_fp_per_game")],
        "games": [F("current_games_played")],
        "hotness": [],
    }
    DEFAULT_SORT = "name"

    def get_queryset(self):
        qs = Player.objects.select_related("team").prefetch_related(
            Prefetch("snapshots", queryset=PlayerSnapshot.objects.order_by("-as_of")),
            Prefetch(
                "injuries",
                queryset=PlayerInjury.objects.order_by("-observed_at", "-created_at"),
                to_attr="injury_history",
            ),
        )

        watched = WatchlistEntry.objects.filter(
            user=self.request.user,
            player_id=OuterRef("pk"),
        )
        qs = qs.annotate(is_watched=Exists(watched))
        watchlist_only = self.request.GET.get("watchlist") == "1" or getattr(
            self, "watchlist_page", False
        )
        if watchlist_only:
            qs = qs.filter(is_watched=True)

        roster_id = self.request.GET.get("roster", "").strip()
        if roster_id and Roster.objects.filter(
            id=roster_id, manager__user=self.request.user
        ).exists():
            qs = qs.filter(
                roster_memberships__roster_id=roster_id,
                roster_memberships__removed_at__isnull=True,
            )

        # Retired and released players stay in the table so their history keeps
        # resolving, but they are noise on a roster screen unless asked for.
        if not self.request.GET.get("inactive") and not getattr(self, "watchlist_page", False):
            qs = qs.filter(is_active=True)

        # Each term has to match somewhere, so "james l" finds LeBron James
        # while a single "james" still matches the surname on its own.
        for term in self.request.GET.get("q", "").split():
            qs = qs.filter(Q(first_name__icontains=term) | Q(last_name__icontains=term))

        if (position := self.request.GET.get("position")) in Player.Position.values:
            qs = qs.filter(position=position)

        if team := self.request.GET.get("team", "").strip():
            qs = qs.filter(team__abbreviation=team)

        injury_status = (self.request.GET.get("injury_status") or "").strip().lower()
        if injury_status in {"healthy", "injured"}:
            latest_injury_status = Subquery(
                PlayerInjury.objects.filter(player_id=OuterRef("pk"))
                .order_by("-observed_at", "-created_at")
                .values("status")[:1]
            )
            qs = qs.annotate(latest_injury_status=latest_injury_status)
            if injury_status == "injured":
                for healthy_state in ("healthy", "available", "returned", "cleared"):
                    qs = qs.exclude(latest_injury_status__iexact=healthy_state)
                qs = qs.exclude(latest_injury_status__isnull=True)
            else:
                qs = qs.filter(
                    Q(latest_injury_status__isnull=True)
                    | Q(latest_injury_status__iexact="healthy")
                    | Q(latest_injury_status__iexact="available")
                    | Q(latest_injury_status__iexact="returned")
                    | Q(latest_injury_status__iexact="cleared")
                )

        if self.request.GET.get("min_games") == "6":
            qs = qs.filter(current_games_played__gte=6)

        try:
            max_salary = Decimal(self.request.GET.get("max_salary", ""))
        except InvalidOperation:
            max_salary = None
        if max_salary is not None and max_salary >= 0:
            qs = qs.filter(current_salary__lte=max_salary * 1_000_000)

        fantasy_points = F("current_fp_per_game")
        expected_salary_millions = (
            Value(Decimal("8.753479e-6"))
            * fantasy_points
            * fantasy_points
            * fantasy_points
            * fantasy_points
            + Value(Decimal("-8.583448e-4")) * fantasy_points * fantasy_points * fantasy_points
            + Value(Decimal("3.058140e-2")) * fantasy_points * fantasy_points
            + Value(Decimal("-5.762906e-2")) * fantasy_points
            + Value(Decimal("4.668975"))
        )
        expected_salary = ExpressionWrapper(
            expected_salary_millions * Value(Decimal("1000000")),
            output_field=DecimalField(max_digits=20, decimal_places=2),
        )
        qs = qs.annotate(
            predicted_salary_amount=Case(
                When(
                    current_fp_per_game__isnull=False,
                    then=Greatest(Value(Decimal("0.00")), expected_salary),
                ),
                output_field=DecimalField(max_digits=20, decimal_places=2),
            )
        ).annotate(
            salary_difference_amount=ExpressionWrapper(
                F("predicted_salary_amount") - F("current_salary"),
                output_field=DecimalField(max_digits=20, decimal_places=2),
            )
        )

        sort, descending = self._sort_params()
        if sort == "hotness":
            players = list(qs)
            scored = []
            unscored = []
            for player in players:
                score = player.hotness_score()
                if score is None:
                    unscored.append(player)
                    continue
                increases, comparisons = (int(value) for value in score.split("/"))
                scored.append((Decimal(increases) / Decimal(comparisons), comparisons, player))
            scored.sort(key=lambda item: (item[0], item[1]), reverse=descending)
            return [player for _, _, player in scored] + unscored

        order_fields = self.SORT_FIELDS[sort]
        if descending:
            order_fields = [
                f.desc(nulls_last=True) if isinstance(f, F) else f"-{f}" for f in order_fields
            ]
        else:
            order_fields = [f.asc(nulls_last=True) if isinstance(f, F) else f for f in order_fields]
        return qs.order_by(*order_fields)

    def _sort_params(self):
        sort = self.request.GET.get("sort", self.DEFAULT_SORT)
        if sort not in self.SORT_FIELDS:
            sort = self.DEFAULT_SORT
        direction = self.request.GET.get("dir")
        if direction is None:
            return sort, sort in {"avg", "points", "hotness"}
        return sort, direction == "desc"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["watchlist_only"] = self.request.GET.get("watchlist") == "1" or getattr(
            self, "watchlist_page", False
        )
        context["watchlist_page"] = getattr(self, "watchlist_page", False)
        context["positions"] = Player.Position.choices
        context["teams"] = Team.objects.order_by("name")
        context["rosters"] = Roster.objects.filter(
            manager__user=self.request.user
        ).select_related("manager", "season")
        context["salary_fp_chart"] = charts.scatter_chart(
            (
                player.full_name,
                player.current_fp_per_game,
                player.current_salary / 1_000_000 if player.current_salary is not None else None,
                player.predicted_salary,
                {
                    "detail_url": reverse("nba:player-detail", args=[player.slug]),
                    "total_fp": player.current_total_fp,
                    "games_played": player.current_games_played,
                    "difference": (
                        player.predicted_salary - player.current_salary / 1_000_000
                        if player.predicted_salary is not None and player.current_salary is not None
                        else None
                    ),
                    "hotness": player.hotness_score(),
                },
            )
            for player in self.object_list
        )
        sort, descending = self._sort_params()
        context["current_sort"] = sort
        context["current_dir"] = "desc" if descending else "asc"
        return context

    def get_template_names(self):
        # HTMX filter requests only need the table swapped in.
        if self.request.htmx:
            return ["nba/partials/player_table.html"]
        return super().get_template_names()


class WatchlistView(PlayerListView):
    watchlist_page = True
    template_name = "nba/player_list.html"


class WatchlistToggleView(LoginRequiredMixin, View):
    def post(self, request, slug):
        player = get_object_or_404(Player, slug=slug)
        entry = WatchlistEntry.objects.filter(user=request.user, player=player).first()
        if entry is None:
            services.add_to_watchlist(request.user, player)
        else:
            services.remove_from_watchlist(request.user, player)

        if entry is not None and request.GET.get("remove_row") == "1":
            return HttpResponse("")

        return render(
            request,
            "nba/partials/watchlist_button.html",
            {
                "player": player,
                "is_watched": entry is None,
            },
        )


def _card(title, unit, rows, footnote=""):
    """A statistics card for the team detail page.

    Rows are built here rather than in the template because the two cards do
    not share a shape: a sum of salaries is a meaningful number, a sum of
    per-game averages is not. Missing figures are dropped, so a card either
    shows a row or does not claim it.
    """
    return {
        "title": title,
        "unit": unit,
        "rows": [(label, value) for label, value in rows if value is not None],
        "footnote": footnote,
    }


def _coverage(summary, total):
    """A note naming how many players a card is based on, when it is not all."""
    if summary["n"] == 0 or summary["n"] == total:
        return ""
    return f"From {summary['n']} of {total} players. No value on record for the rest."


class TeamDetailView(LoginRequiredMixin, DetailView):
    """One franchise: its players, and what they cost and score.

    The salary and points figures come straight off the `current_*` columns on
    Player, so this page is one query for the team and one for its players --
    no aggregation over the snapshot history.
    """

    template_name = "nba/team_detail.html"
    context_object_name = "team"

    SORT_FIELDS = {
        "name": ["last_name", "first_name"],
        "position": ["position"],
        "team": ["team__name"],
        "salary": [F("current_salary")],
        "expected": [F("predicted_salary_amount")],
        "difference": [F("salary_difference_amount")],
        "points": [F("current_total_fp")],
        "avg": [F("current_fp_per_game")],
        "games": [F("current_games_played")],
        "hotness": [],
    }
    DEFAULT_SORT = "avg"

    def get_object(self, queryset=None):
        # Looked up by abbreviation, case-insensitively, so both /teams/lal/
        # and a hand-typed /teams/LAL/ arrive here.
        return get_object_or_404(Team, abbreviation__iexact=self.kwargs["abbreviation"])

    def _sort_params(self):
        sort = self.request.GET.get("sort", self.DEFAULT_SORT)
        if sort not in self.SORT_FIELDS:
            sort = self.DEFAULT_SORT
        direction = self.request.GET.get("dir")
        if direction is None:
            return sort, sort in {"avg", "points", "hotness"}
        return sort, direction == "desc"

    def _players(self):
        qs = self.object.players.filter(is_active=True).select_related("team").prefetch_related(
            Prefetch("snapshots", queryset=PlayerSnapshot.objects.order_by("-as_of"))
        )

        fantasy_points = F("current_fp_per_game")
        expected_salary_millions = (
            Value(Decimal("8.753479e-6"))
            * fantasy_points
            * fantasy_points
            * fantasy_points
            * fantasy_points
            + Value(Decimal("-8.583448e-4")) * fantasy_points * fantasy_points * fantasy_points
            + Value(Decimal("3.058140e-2")) * fantasy_points * fantasy_points
            + Value(Decimal("-5.762906e-2")) * fantasy_points
            + Value(Decimal("4.668975"))
        )
        expected_salary = ExpressionWrapper(
            expected_salary_millions * Value(Decimal("1000000")),
            output_field=DecimalField(max_digits=20, decimal_places=2),
        )
        qs = qs.annotate(
            predicted_salary_amount=Case(
                When(
                    current_fp_per_game__isnull=False,
                    then=Greatest(Value(Decimal("0.00")), expected_salary),
                ),
                output_field=DecimalField(max_digits=20, decimal_places=2),
            )
        ).annotate(
            salary_difference_amount=ExpressionWrapper(
                F("predicted_salary_amount") - F("current_salary"),
                output_field=DecimalField(max_digits=20, decimal_places=2),
            )
        )

        sort, descending = self._sort_params()
        if sort == "hotness":
            players = list(qs)
            scored = []
            unscored = []
            for player in players:
                score = player.hotness_score()
                if score is None:
                    unscored.append(player)
                    continue
                increases, comparisons = (int(value) for value in score.split("/"))
                scored.append((Decimal(increases) / Decimal(comparisons), comparisons, player))
            scored.sort(key=lambda item: (item[0], item[1]), reverse=descending)
            return [player for _, _, player in scored] + unscored

        order_fields = self.SORT_FIELDS[sort]
        if descending:
            order_fields = [
                f.desc(nulls_last=True) if isinstance(f, F) else f"-{f}" for f in order_fields
            ]
        else:
            order_fields = [f.asc(nulls_last=True) if isinstance(f, F) else f for f in order_fields]
        return list(qs.order_by(*order_fields))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        players = self._players()
        salary = stats.summarise(player.current_salary for player in players)
        per_game = stats.summarise(player.current_fp_per_game for player in players)
        points = stats.summarise(player.current_total_fp for player in players)

        context["players"] = players
        context["cards"] = [
            _card(
                "Salaries",
                "money",
                [
                    ("Average", salary["avg"]),
                    ("Median", salary["median"]),
                    ("Lowest", salary["min"]),
                    ("Highest", salary["max"]),
                    ("Total", salary["total"]),
                ],
                _coverage(salary, len(players)),
            ),
            _card(
                "Fantasy points",
                "points",
                [
                    ("Avg per game", per_game["avg"]),
                    ("Median per game", per_game["median"]),
                    ("Fewest per game", per_game["min"]),
                    ("Most per game", per_game["max"]),
                    ("Points total", points["total"]),
                    # The team's real rate, which the four per-game rows above
                    # are not: they average players, this divides totals.
                    ("Team per game", stats.scoring_rate(players)),
                ],
                _coverage(per_game, len(players)),
            ),
        ]
        context["inactive_count"] = self.object.players.filter(is_active=False).count()
        context["salary_fp_chart"] = charts.scatter_chart(
            (
                player.full_name,
                player.current_fp_per_game,
                player.current_salary / 1_000_000 if player.current_salary is not None else None,
                player.predicted_salary,
                {
                    "detail_url": reverse("nba:player-detail", args=[player.slug]),
                    "total_fp": player.current_total_fp,
                    "games_played": player.current_games_played,
                    "difference": (
                        player.predicted_salary - player.current_salary / 1_000_000
                        if player.predicted_salary is not None and player.current_salary is not None
                        else None
                    ),
                    "hotness": player.hotness_score(),
                },
            )
            for player in players
        )
        sort, descending = self._sort_params()
        context["current_sort"] = sort
        context["current_dir"] = "desc" if descending else "asc"
        return context


def _change(newer, older):
    """What moved between two snapshots -- None where either figure is missing.

    A missing value is not a change of zero: before his first game a player has
    no per-game average to be compared against, and printing 0,00 there would
    claim his form held steady.
    """
    if newer is None or older is None:
        return None
    return newer - older


class PlayerDetailView(LoginRequiredMixin, DetailView):
    """One player, week by week: what the game charged and what he returned.

    Inactive players are reachable here on purpose. Their snapshots still
    exist, and a history page that stops resolving the moment someone drops out
    of the source is not a history.
    """

    template_name = "nba/player_detail.html"
    context_object_name = "player"
    queryset = Player.objects.select_related("team")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Newest first, which is both the order the table shows and the order
        # that makes each row's predecessor the next item in the list.
        snapshots = list(self.object.snapshots.select_related("season", "team").order_by("-as_of"))

        rows = []
        for index, snapshot in enumerate(snapshots):
            # The row beneath this one in the table: the Sunday before. The
            # oldest row has none, and `getattr(None, field, None)` is what
            # leaves it without changes rather than with changes of zero.
            previous = snapshots[index + 1] if index + 1 < len(snapshots) else None
            changes = {
                f"{field}_change": _change(getattr(snapshot, field), getattr(previous, field, None))
                for field in ("salary", "total_fp", "fp_per_game", "games_played")
            }
            rows.append({"snapshot": snapshot, **changes})

        context["rows"] = rows
        context["is_watched"] = WatchlistEntry.objects.filter(
            user=self.request.user, player=self.object
        ).exists()
        latest_injury = self.object.injuries.order_by("-observed_at", "-created_at").first()
        context["latest_injury"] = latest_injury
        context["is_injured"] = bool(
            latest_injury
            and latest_injury.status.casefold()
            not in {"healthy", "available", "returned", "cleared"}
        )
        context["hotness_score"] = self.object.hotness_score()
        # Oldest on the left, which is the opposite of the table below it: a
        # line is read forwards through the season, a table newest-first.
        # `fp_per_game` is already an average -- points over games played to
        # that Sunday -- so the line is the running average, not six weekly
        # readings, and it moves less the further into the season it gets.
        context["fp_chart"] = charts.line_chart(
            [
                (
                    formats.date_format(timezone.localtime(snapshot.as_of), "d.m."),
                    snapshot.fp_per_game,
                )
                for snapshot in reversed(snapshots)
            ]
        )
        # The headline of the page. Only meaningful with two snapshots to
        # compare, so a single one leaves it out rather than reporting that
        # nothing has changed since itself.
        if len(snapshots) > 1:
            context["first_as_of"] = snapshots[-1].as_of
            context["salary_since_first"] = snapshots[0].salary - snapshots[-1].salary
        return context
