"""Trade plans: variants of trades a roster might make, and how each would go.

Kept out of `views.py`, which is long enough already, the way `nba` keeps its
news screens in `news_views.py`. Every screen here reads; the only writes are
to the plan itself, through `services`. Nothing touches the roster.
"""

import datetime as dt

from django.core.exceptions import ValidationError
from django.db.models import Prefetch, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views import View
from django.views.generic import TemplateView

from apps.core.htmx import add_toast
from apps.core.views import ConfirmView, PromptView
from apps.nba.models import Player
from apps.nba.salary_outlook import salary_baseline

from . import services, simulator
from .models import (
    MINIMUM_BY_POSITION,
    TRADE_BUY_PRICE,
    TRADE_SELL_PRICE,
    PlanStep,
    PlayerSnapshot,
    TradePlan,
)
from .views import OwnRosterMixin, _mark_kicked, _picker_filters, _watchlist_context


class OwnPlanMixin(OwnRosterMixin):
    """A plan of one of your own rosters; anybody else's is a 404."""

    def get_plan(self):
        if not hasattr(self, "_plan"):
            roster = self.get_roster()
            self._plan = get_object_or_404(TradePlan, pk=self.kwargs["plan_pk"], roster=roster)
            # The roster already loaded, squad and all, rather than a second copy.
            self._plan.roster = roster
        return self._plan


def plan_context(plan, now=None, read_only=False, result=None):
    """Everything the plan page and its swappable body render from."""
    now = now or timezone.now()
    result = result or simulator.simulate(plan, now=now)
    baseline = salary_baseline(plan.roster.season, now=now)
    return {
        "plan": plan,
        "roster": plan.roster,
        "result": result,
        "read_only": read_only,
        "next_update": baseline.next_update if baseline else None,
        "first_day": simulator.next_processing_day(now),
        "trade_buy_price": TRADE_BUY_PRICE,
    }


class PlanListView(OwnRosterMixin, TemplateView):
    """Every variant of one roster side by side: which work, and which gains most."""

    template_name = "fantasy/plan_list.html"

    def get_context_data(self, **kwargs):
        roster = self.get_roster()
        now = timezone.now()
        results = []
        for plan in roster.trade_plans.prefetch_related("steps"):
            plan.roster = roster
            results.append(simulator.simulate(plan, now=now, steps=list(plan.steps.all())))

        # "Best" only means something among plans that work and have moves to
        # compare, and only once there are at least two to choose between.
        candidates = [
            result
            for result in results
            if result.works and result.has_steps and result.fp_rest is not None
        ]
        best = max(candidates, key=lambda result: result.fp_rest) if len(candidates) > 1 else None
        baseline = salary_baseline(roster.season, now=now)
        return {
            **super().get_context_data(**kwargs),
            "roster": roster,
            "results": results,
            "best": best,
            "next_update": baseline.next_update if baseline else None,
            "first_day": simulator.next_processing_day(now),
        }


class PlanDetailView(OwnPlanMixin, TemplateView):
    template_name = "fantasy/plan_detail.html"

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), **plan_context(self.get_plan())}


def render_plan_body(request, plan, message=None):
    """The plan's summary and timeline, swapped in after an edit made on the page."""
    response = render(request, "fantasy/partials/plan_body.html", plan_context(plan))
    if message:
        add_toast(response, message)
    return response


class PlanCreateView(OwnRosterMixin, PromptView):
    title = gettext_lazy("New variant")
    body = gettext_lazy(
        "A plan of trades you might make over the coming days. Nothing on the roster changes."
    )
    field_label = gettext_lazy("What should this variant be called?")
    field_name = "name"
    submit_label = gettext_lazy("Create variant")

    def initial_value(self, obj):
        return services.default_plan_name(self.get_roster())

    def clean(self, value, obj, choice=None):
        return value

    def perform(self, obj, value, choice=None):
        return services.create_plan(self.get_roster(), value)

    def get_success_url(self, result):
        return reverse("fantasy:plan-detail", args=[result.roster_id, result.pk])


class PlanRenameView(OwnPlanMixin, PromptView):
    title = gettext_lazy("Rename variant")
    field_label = gettext_lazy("Name")
    field_name = "name"
    submit_label = gettext_lazy("Save")

    def get_object(self):
        return self.get_plan()

    def initial_value(self, obj):
        return obj.name

    def clean(self, value, obj, choice=None):
        return value

    def perform(self, obj, value, choice=None):
        return services.rename_plan(obj, value)

    def get_success_url(self, result):
        return reverse("fantasy:plan-detail", args=[result.roster_id, result.pk])


class PlanDuplicateView(OwnPlanMixin, View):
    """Copy a variant with all its moves, and open the copy."""

    def post(self, request, *args, **kwargs):
        copy = services.duplicate_plan(self.get_plan())
        url = reverse("fantasy:plan-detail", args=[copy.roster_id, copy.pk])
        if not request.htmx:
            return redirect(url)
        response = HttpResponse(status=204)
        response["HX-Redirect"] = url
        return response


class PlanDeleteView(OwnPlanMixin, ConfirmView):
    title = gettext_lazy("Delete this variant?")
    confirm_label = gettext_lazy("Delete variant")
    tone = "danger"

    def get_object(self):
        return self.get_plan()

    def get_body(self, obj):
        return _("“%(name)s” and its moves will be deleted. The roster itself is not affected.") % {
            "name": obj.name
        }

    def perform(self, obj):
        obj.delete()

    def get_success_url(self, obj):
        return reverse("fantasy:plan-list", args=[obj.roster_id])


class PlanStepDeleteView(OwnPlanMixin, View):
    def post(self, request, *args, **kwargs):
        plan = self.get_plan()
        step = get_object_or_404(PlanStep, pk=self.kwargs["step_pk"], plan=plan)
        services.delete_plan_step(step)
        return render_plan_body(request, plan, _("Move removed."))


# --- the step editor ------------------------------------------------------------

EDITOR_DAYS = 21  # how far ahead the day strip reaches
EDITOR_MARKET = 60  # incoming players listed at once


def _editor_days(plan, result, first, prices):
    """The processing days the editor offers, with what the strip shows on each."""
    season = plan.roster.season
    planned = {}
    for day in result.days:
        planned[day.date] = len(day.steps)
    days = []
    for offset in range(EDITOR_DAYS):
        date = first + dt.timedelta(days=offset)
        if date > season.ends_on:
            break
        moment = simulator.processing_moment(date)
        days.append(
            {
                "date": date,
                "source": prices.source(moment),
                "is_update": any(update == moment for update in prices.updates),
                "moves": planned.get(date, 0),
            }
        )
    return days


def _chosen(request, key):
    raw = request.GET.get(key) or request.POST.get(key) or ""
    if not raw:
        return None
    try:
        return Player.objects.select_related("team").filter(pk=raw).first()
    except (ValidationError, ValueError):
        return None


def _chosen_day(request, days):
    raw = request.GET.get("day") or request.POST.get("day") or ""
    try:
        wanted = dt.date.fromisoformat(raw)
    except ValueError:
        wanted = None
    offered = [day["date"] for day in days]
    return wanted if wanted in offered else (offered[0] if offered else None)


class PlanStepEditorView(OwnPlanMixin, View):
    """Add a move to a plan: a day, then who goes out and who comes in.

    The trade dialog's twin, with one difference that runs through all of it:
    it is answered for the chosen *day*, not for now. The outgoing list is the
    squad as the plan leaves it by then, the incoming prices are that day's cap
    hits -- predicted ones from a Sunday update on -- and the budget is what
    that day's batch has to spend. The preview replays the whole plan with the
    move in it, so what it says is exactly what the plan page will say.

    As in the trade dialog the half-made choice lives in the query string, and
    `?body=1` asks for the dialog's body only.
    """

    template_name = "fantasy/plan_step_modal.html"
    body_template = "fantasy/partials/plan_step_editor.html"

    def get_context(self, error=None):
        request = self.request
        plan = self.get_plan()
        roster = plan.roster
        now = timezone.now()
        result = simulator.simulate(plan, now=now)
        prices = simulator.Prices(roster.season, now)
        days = _editor_days(plan, result, simulator.next_processing_day(now), prices)
        day = _chosen_day(request, days)
        kind = request.GET.get("kind") or request.POST.get("kind") or PlanStep.Kind.TRADE
        if kind not in PlanStep.Kind.values:
            kind = PlanStep.Kind.TRADE

        context = {
            "plan": plan,
            "roster": roster,
            "days": days,
            "day": day,
            "kind": kind,
            "error": error,
            "panel_class": "modal-panel-xl",
            "editor_url": reverse("fantasy:plan-step-new", args=[roster.pk, plan.pk]),
            "kinds": [
                (PlanStep.Kind.TRADE, _("Trade")),
                (PlanStep.Kind.BUY_TRADE, _("Buy a trade")),
                (PlanStep.Kind.SELL_TRADE, _("Sell a trade")),
            ],
            "trade_buy_price": TRADE_BUY_PRICE,
            "trade_sell_price": TRADE_SELL_PRICE,
        }
        if day is None:
            return context

        source = prices.source(simulator.processing_moment(day))
        squad_ids, cash_before, trades_before = result.state_before(day)
        existing = next((entry for entry in result.days if entry.date == day), None)
        busy = set()
        if existing:
            for outcome in existing.steps:
                busy.update(
                    pk for pk in (outcome.step.player_out_id, outcome.step.player_in_id) if pk
                )

        # Who can leave: the squad as the plan leaves it by that day.
        players = result.players
        missing = [pk for pk in squad_ids if pk not in players]
        if missing:
            players.update(simulator.load_players(missing))
        squad = sorted(
            (players[pk] for pk in squad_ids),
            key=lambda player: (player.position, -(player.current_salary or 0)),
        )
        _mark_kicked(request.user, squad)
        squad.sort(key=lambda player: not player.is_kicked)
        for player in squad:
            player.day_price = prices.price(player, source)
            player.is_busy = player.pk in busy

        player_out = _chosen(request, "out")
        if player_out is not None and (player_out.pk not in squad_ids or player_out.pk in busy):
            player_out = None
        if player_out is not None:
            player_out = players.get(player_out.pk, player_out)
        player_in = _chosen(request, "in")
        if player_in is not None and (player_in.pk in squad_ids or player_in.pk in busy):
            player_in = None

        # What the day's batch has to spend: what it has after its other moves,
        # plus whatever the outgoing player frees up at that day's price.
        day_cash = existing.cash_after if existing else cash_before
        out_price = prices.price(player_out, source) if player_out else None
        budget = day_cash + (out_price or 0)

        filters = _picker_filters(request)
        baseline = prices.baseline
        market = (
            services.with_player_marks(
                Player.objects.filter(is_active=True, current_salary__isnull=False).exclude(
                    pk__in=[*squad_ids, *busy]
                ),
                request.user,
            )
            .select_related("team")
            .prefetch_related(
                Prefetch("snapshots", queryset=PlayerSnapshot.objects.order_by("-as_of"))
            )
            .order_by("-current_salary", "last_name")
        )
        if filters["watchlist"]:
            market = market.filter(is_watched=True)
        for term in filters["q"].split():
            market = market.filter(Q(first_name__icontains=term) | Q(last_name__icontains=term))
        if filters["position"] in Player.Position.values:
            market = market.filter(position=filters["position"])
        available = list(market[:EDITOR_MARKET])
        if player_in is not None and all(player.pk != player_in.pk for player in available):
            available.insert(0, player_in)
        for player in available:
            player.day_price = prices.price(player, source)
            player.affordable = player.day_price is not None and player.day_price <= budget
        if player_in is not None:
            player_in = next(player for player in available if player.pk == player_in.pk)
        if player_out is not None:
            counts = dict.fromkeys(Player.Position.values, 0)
            for pk in squad_ids:
                counts[players[pk].position] += 1
            for player in available:
                after = dict(counts)
                after[player_out.position] -= 1
                after[player.position] += 1
                player.leaves_short = any(
                    after[position] < minimum <= counts[position]
                    for position, minimum in MINIMUM_BY_POSITION.items()
                )

        preview = self.preview(plan, now, day, kind, player_out, player_in)

        context.update(
            {
                "source": source,
                "squad": squad,
                "available": available,
                "filters": filters,
                "positions": Player.Position.choices,
                "player_out": player_out,
                "player_in": player_in,
                "budget": budget,
                "cash_before": cash_before,
                "trades_before": trades_before,
                "existing": existing,
                "preview": preview,
                "ready": kind != PlanStep.Kind.TRADE or (player_out and player_in),
                "mobile_step": 2 if player_out is not None else 1,
                "keep_day": f"&day={day.isoformat()}",
                "keep_out": f"&out={player_out.pk}" if player_out else "",
                "keep_in": f"&in={player_in.pk}" if player_in else "",
                "baseline": baseline,
                **_watchlist_context(roster, request.user, filters),
            }
        )
        return context

    def preview(self, plan, now, day, kind, player_out, player_in):
        """The day as it would come out with the move added, or None until ready."""
        if kind == PlanStep.Kind.TRADE and (player_out is None or player_in is None):
            return None
        draft = PlanStep(
            plan=plan,
            processing_date=day,
            order=10_000,
            kind=kind,
            player_out=player_out if kind == PlanStep.Kind.TRADE else None,
            player_in=player_in if kind == PlanStep.Kind.TRADE else None,
        )
        result = simulator.simulate(plan, now=now, extra_steps=[draft])
        entry = next(entry for entry in result.days if entry.date == day)
        outcome = next(outcome for outcome in entry.steps if outcome.step is draft)
        return {"day": entry, "outcome": outcome, "result": result}

    def get(self, request, *args, **kwargs):
        context = self.get_context()
        template = self.body_template if request.GET.get("body") else self.template_name
        return render(request, template, context)

    def post(self, request, *args, **kwargs):
        context = self.get_context()
        plan = context["plan"]
        try:
            if context["day"] is None:
                raise ValidationError(_("There is no processing day left this season."))
            services.add_plan_step(
                plan,
                context["day"],
                kind=context["kind"],
                player_out=context.get("player_out"),
                player_in=context.get("player_in"),
            )
        except ValidationError as exc:
            if request.GET.get("direct"):
                # A one-click fix from the plan page: the page stays, the toast says why.
                return add_toast(HttpResponse(status=204), exc.messages[0], level="error")
            response = render(request, self.body_template, self.get_context(error=exc.messages[0]))
            response["HX-Retarget"] = "#plan-editor"
            response["HX-Reswap"] = "outerHTML"
            return response

        response = render_plan_body(request, plan, _("Move added."))
        response["HX-Trigger"] = "close-modal"
        return response


class PlanStepMoveView(OwnPlanMixin, View):
    """Put one move on another processing day."""

    template_name = "fantasy/plan_step_move.html"

    def get_step(self):
        return get_object_or_404(
            PlanStep.objects.select_related("player_out", "player_in"),
            pk=self.kwargs["step_pk"],
            plan=self.get_plan(),
        )

    def get_context(self, step, error=None):
        plan = self.get_plan()
        now = timezone.now()
        result = simulator.simulate(plan, now=now)
        prices = simulator.Prices(plan.roster.season, now)
        return {
            "plan": plan,
            "roster": plan.roster,
            "step": step,
            "days": _editor_days(plan, result, simulator.next_processing_day(now), prices),
            "error": error,
        }

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name, self.get_context(self.get_step()))

    def post(self, request, *args, **kwargs):
        step = self.get_step()
        try:
            day = dt.date.fromisoformat(request.POST.get("day", ""))
            services.move_plan_step(step, day)
        except (ValueError, ValidationError) as exc:
            message = exc.messages[0] if isinstance(exc, ValidationError) else _("Pick a day.")
            return render(request, self.template_name, self.get_context(step, error=message))
        response = render_plan_body(request, self.get_plan(), _("Move moved."))
        response["HX-Trigger"] = "close-modal"
        response["HX-Retarget"] = "#plan-body"
        response["HX-Reswap"] = "outerHTML"
        return response
