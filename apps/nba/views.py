from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

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
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import formats, timezone
from django.utils.translation import gettext as _
from django.views import View
from django.views.generic import DetailView, ListView

from apps.fantasy import services
from apps.fantasy.models import PlayerSnapshot, Roster, WatchlistEntry

from . import charts, stats
from .forms import PlayerNoteForm, TeamNoteForm
from .models import Player, PlayerInjury, PlayerNote, Team, TeamNote
from .services import DailyApiLimitExceeded, NbaApiError, sync_injuries


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


class InjuryRefreshView(LoginRequiredMixin, View):
    """Synchronize injuries once per day and return the action state for HTMX."""

    template_name = "nba/partials/injury_refresh.html"

    def post(self, request):
        try:
            summary = sync_injuries()
        except DailyApiLimitExceeded:
            context = {
                "injury_refresh_error": _("The injury report was already updated today. The next update can be invoked tomorrow.")
            }
        except NbaApiError:
            context = {
                "injury_refresh_error": _("The injury report could not be updated. Please try again tomorrow.")
            }
        else:
            context = {
                "injury_refresh_success": _(
                    "Injury report updated: %(created)d created, %(updated)d updated, "
                    "%(unmatched)d unmatched."
                )
                % summary
            }
        return render(request, self.template_name, context)


class PlayerCompareView(LoginRequiredMixin, ListView):
    model = Player
    template_name = "nba/player_compare.html"
    context_object_name = "players"

    def get_queryset(self):
        qs = Player.objects.filter(is_active=True).select_related("team").order_by("last_name", "first_name")
        query = self.request.GET.get("q", "").strip()
        if query:
            for term in query.split():
                qs = qs.filter(Q(first_name__icontains=term) | Q(last_name__icontains=term))
        return qs[:50]

    def _selected_slugs(self):
        selected = []
        seen = set()
        for slug in self.request.GET.getlist("player"):
            value = (slug or "").strip()
            if value and value not in seen:
                selected.append(value)
                seen.add(value)
        return selected[:5]

    def _compare_metric(self, player):
        hotness = player.hotness_score()
        salary_gap = None
        if player.current_salary is not None and player.predicted_salary is not None:
            salary_gap = player.predicted_salary - (player.current_salary / Decimal("1000000"))

        if player.is_injured:
            roster_fit = "Injury risk"
        elif salary_gap is not None and salary_gap > 0:
            roster_fit = "Value buy"
        elif player.current_fp_per_game is not None and player.current_fp_per_game >= 18:
            roster_fit = "High upside"
        elif player.current_fp_per_game is not None and player.current_fp_per_game >= 12:
            roster_fit = "Balanced"
        else:
            roster_fit = "Depth"

        return {
            "player": player,
            "salary_gap": salary_gap,
            "value_label": "Value buy" if salary_gap is not None and salary_gap > 0 else "Premium",
            "trend_label": hotness or "No recent trend",
            "roster_fit": roster_fit,
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        selected_slugs = self._selected_slugs()

        qs = (
            Player.objects.filter(slug__in=selected_slugs)
            .select_related("team")
            .prefetch_related(
                Prefetch(
                    "injuries",
                    queryset=PlayerInjury.objects.order_by("-observed_at", "-created_at"),
                    to_attr="injury_history",
                ),
                Prefetch("snapshots", queryset=PlayerSnapshot.objects.order_by("-as_of")),
            )
        )
        if self.request.user.is_authenticated:
            qs = qs.annotate(
                is_watched=Exists(
                    WatchlistEntry.objects.filter(
                        user=self.request.user, player=OuterRef("pk")
                    )
                )
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

        selected_players = list(qs)
        selected_by_slug = {player.slug: player for player in selected_players}
        ordered_selected = [selected_by_slug[slug] for slug in selected_slugs if slug in selected_by_slug]

        selected_player_items = []
        for player in ordered_selected:
            other_slugs = [s for s in selected_slugs if s != player.slug]
            remove_query = urlencode([("player", s) for s in other_slugs])
            selected_player_items.append(
                {
                    "player": player,
                    "remove_query": remove_query,
                    "metric": self._compare_metric(player),
                }
            )

        modal_query = urlencode([("player", s) for s in selected_slugs])

        context["selected_players"] = ordered_selected
        context["selected_player_items"] = selected_player_items
        context["selected_slug_set"] = set(selected_slugs)
        context["selected_slugs"] = selected_slugs
        context["modal_query"] = modal_query
        context["search_query"] = self.request.GET.get("q", "").strip()
        context["max_compare_players"] = 5
        context["can_add_more"] = len(selected_slugs) < 5
        context["search_results"] = list(self.get_queryset())
        if selected_slugs:
            context["search_results"] = [
                player for player in context["search_results"] if player.slug not in selected_slugs
            ]
        context["compare_metrics"] = [item["metric"] for item in selected_player_items]
        return context


class PlayerCompareModalView(LoginRequiredMixin, View):
    template_name = "nba/player_compare_modal.html"
    results_template = "nba/partials/compare_search_results.html"

    def _selected_slugs(self):
        selected = []
        seen = set()
        for slug in self.request.GET.getlist("player"):
            value = (slug or "").strip()
            if value and value not in seen:
                selected.append(value)
                seen.add(value)
        return selected[:5]

    def get(self, request, *args, **kwargs):
        selected_slugs = self._selected_slugs()
        search_query = request.GET.get("q", "").strip()

        qs = Player.objects.filter(is_active=True).select_related("team")
        if selected_slugs:
            qs = qs.exclude(slug__in=selected_slugs)

        if search_query:
            for term in search_query.split():
                qs = qs.filter(
                    Q(first_name__icontains=term)
                    | Q(last_name__icontains=term)
                    | Q(team__name__icontains=term)
                    | Q(team__abbreviation__icontains=term)
                )

        players_list = list(qs.order_by("last_name", "first_name")[:30])

        matching_players = []
        for player in players_list:
            add_query = urlencode([("player", s) for s in selected_slugs + [player.slug]])
            matching_players.append(
                {
                    "player": player,
                    "add_query": add_query,
                }
            )

        context = {
            "search_query": search_query,
            "matching_players": matching_players,
            "selected_slugs": selected_slugs,
            "selected_count": len(selected_slugs),
            "max_compare_players": 5,
            "can_add_more": len(selected_slugs) < 5,
            "panel_class": "modal-panel-wide",
        }

        if request.GET.get("body") or request.headers.get("HX-Target") == "compare-modal-results":
            return render(request, self.results_template, context)
        return render(request, self.template_name, context)


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

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        form = TeamNoteForm(request.POST)
        if form.is_valid():
            TeamNote.objects.create(
                user=request.user,
                team=self.object,
                content=form.cleaned_data["content"],
            )
            return self.get_success_url()
        return self.render_to_response(self.get_context_data(form=form))

    def get_success_url(self):
        return HttpResponseRedirect(
            reverse("nba:team-detail", args=[self.object.abbreviation.lower()])
        )

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
            Prefetch("snapshots", queryset=PlayerSnapshot.objects.order_by("-as_of")),
            Prefetch(
                "injuries",
                queryset=PlayerInjury.objects.order_by("-observed_at", "-created_at"),
                to_attr="injury_history",
            ),
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
        injured_count = sum(1 for player in players if player.is_injured)

        context["players"] = players
        context["cards"] = [
            _card(
            _("Salaries"),
                "money",
                [
                    (_("Average"), salary["avg"]),
                    (_("Median"), salary["median"]),
                    (_("Lowest"), salary["min"]),
                    (_("Highest"), salary["max"]),
                    (_("Total"), salary["total"]),
                ],
                _coverage(salary, len(players)),
            ),
            _card(
                _("Fantasy points"),
                "points",
                [
                    (_("Avg per game"), per_game["avg"]),
                    (_("Median per game"), per_game["median"]),
                    (_("Fewest per game"), per_game["min"]),
                    (_("Most per game"), per_game["max"]),
                    (_("Points total"), points["total"]),
                    # The team's real rate, which the four per-game rows above
                    # are not: they average players, this divides totals.
                    (_("Team per game"), stats.scoring_rate(players)),
                ],
                _coverage(per_game, len(players)),
            ),
            _card(
                _("Player health"),
                "count",
                [
                    (_("Healthy"), len(players) - injured_count),
                    (_("Injured"), injured_count),
                ],
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
        context["notes"] = self.object.notes.filter(user=self.request.user).order_by("-created_at")
        context["note_form"] = kwargs.get("form") or TeamNoteForm()
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

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        form = PlayerNoteForm(request.POST)
        if form.is_valid():
            PlayerNote.objects.create(
                user=request.user,
                player=self.object,
                content=form.cleaned_data["content"],
            )
            return self.get_success_url()
        return self.render_to_response(self.get_context_data(form=form))

    def get_success_url(self):
        return HttpResponseRedirect(reverse("nba:player-detail", args=[self.object.slug]))

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
        context["notes"] = self.object.notes.filter(user=self.request.user).order_by("-created_at")
        context["note_form"] = kwargs.get("form") or PlayerNoteForm()
        context["note"] = kwargs.get("note")
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
