"""Trade plans: variants of trades a roster might make, and how each would go.

Kept out of `views.py`, which is long enough already, the way `nba` keeps its
news screens in `news_views.py`. Every screen here reads; the only writes are
to the plan itself, through `services`. Nothing touches the roster.
"""

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
from apps.nba.salary_outlook import salary_baseline

from . import services, simulator
from .models import PlanStep, TradePlan
from .views import OwnRosterMixin


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
