"""The Reports page: published AI trade reports, and the staff controls for runs.

Players read; staff also start runs, publish or hide them, retry failures,
rerun a run's exact inputs with another model, and change the settings the
Sunday run uses. Staff actions are plain POSTs that redirect back with a
message; HTMX only refreshes the cost estimate and a run in progress.
"""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views import View
from django.views.generic import TemplateView

from apps.core.views import StaffRequiredMixin
from apps.fantasy.models import Roster, Season

from . import catalog, costs, inputs, pages, services
from .forms import ACTIVE, BRIEFING, CHOSEN, RerunForm, RunForm, SettingsForm
from .models import Report, ReportRun, ReportSettings


def _current_season():
    season = Season.objects.filter(is_current=True).first()
    if season is None:
        raise Http404
    return season


class ReportsView(LoginRequiredMixin, TemplateView):
    """A run of the current season: the newest published one by default.

    Players only ever see published runs; staff can open any run by its URL.
    `?roster=<id>` lets staff look at any roster's section of a run.
    """

    template_name = "reports/reports.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        season = Season.objects.filter(is_current=True).first()
        context["season"] = season
        if season is None:
            return context
        runs = pages.published_runs(season)
        run_id = kwargs.get("run_id")
        if run_id is not None:
            run = get_object_or_404(ReportRun, pk=run_id, season=season)
            if not pages.can_see(run, self.request.user):
                raise Http404
        else:
            run = runs[0] if runs else None
        context["runs"] = runs
        if run is not None:
            context.update(
                pages.page(run, self.request.user, roster_id=self.request.GET.get("roster"))
            )
        if self.request.user.is_staff:
            context["staff"] = _staff_context(season, run)
        return context


def _staff_context(season, run):
    report_settings = ReportSettings.load()
    rosters = list(
        Roster.objects.filter(season=season).select_related("manager").order_by("name", "pk")
    )
    all_runs = list(
        ReportRun.objects.filter(season=season)
        .annotate(
            report_count=Count("reports"),
            succeeded=Count("reports", filter=Q(reports__status=Report.Status.SUCCEEDED)),
            failed=Count("reports", filter=Q(reports__status=Report.Status.FAILED)),
            cost=Sum("reports__cost_usd"),
        )
        .order_by("-created_at")
    )
    spent = costs.month_to_date()
    limit = costs.monthly_limit()
    return {
        "settings": report_settings,
        "settings_form": SettingsForm(instance=report_settings),
        "models": catalog.MODELS,
        "efforts": catalog.EFFORTS,
        "rosters": rosters,
        "active_count": sum(1 for roster in rosters if roster.is_active),
        "runs": all_runs,
        "spent": spent,
        "limit": limit,
        "spent_share": min(round(spent / limit * 100), 100) if limit else 100,
        "unavailable": services.unavailable_reason(),
        "open": run is None or any(entry.is_active for entry in all_runs),
    }


def _back(request, run=None):
    """Back to the page the action came from: the run's, or the index."""
    target = reverse("reports:run", args=[run.pk]) if run else reverse("reports:index")
    return redirect(f"{target}#staff")


class StartRunView(StaffRequiredMixin, View):
    def post(self, request):
        season = _current_season()
        form = RunForm(request.POST, season=season)
        if not form.is_valid():
            errors = [error for field_errors in form.errors.values() for error in field_errors]
            messages.error(request, " ".join(errors))
            return _back(request)
        try:
            run = services.start_run(
                season,
                trigger=ReportRun.Trigger.MANUAL,
                started_by=request.user,
                **form.run_options(),
            )
        except services.ReportsUnavailable as error:
            messages.error(request, str(error))
            return _back(request)
        messages.success(
            request,
            _("Run started with %(model)s. It is not published until you publish it.")
            % {"model": run.model_name},
        )
        return _back(request, run)


class PublishView(StaffRequiredMixin, View):
    def post(self, request, run_id):
        run = get_object_or_404(ReportRun, pk=run_id)
        if request.POST.get("publish") == "1":
            try:
                services.publish(run, request.user)
            except services.ReportsUnavailable as error:
                messages.error(request, str(error))
                return _back(request, run)
            messages.success(request, _("Published: every manager can read this report now."))
        else:
            services.unpublish(run)
            messages.success(request, _("Hidden: only staff can see this report now."))
        return _back(request, run)


class RetryView(StaffRequiredMixin, View):
    def post(self, request, run_id):
        run = get_object_or_404(ReportRun, pk=run_id)
        try:
            count = services.retry_failed(run)
        except services.ReportsUnavailable as error:
            messages.error(request, str(error))
            return _back(request, run)
        messages.success(
            request,
            ngettext(
                "Retrying %(count)s failed report.", "Retrying %(count)s failed reports.", count
            )
            % {"count": count}
            if count
            else _("Nothing to retry: no report in this run failed."),
        )
        return _back(request, run)


class RerunView(StaffRequiredMixin, View):
    def post(self, request, run_id):
        run = get_object_or_404(ReportRun, pk=run_id)
        form = RerunForm(request.POST)
        if not form.is_valid():
            messages.error(request, _("Choose a model and an effort for the rerun."))
            return _back(request, run)
        try:
            rerun = services.rerun_with_model(
                run,
                model=form.cleaned_data["model"],
                effort=form.cleaned_data["effort"],
                label=form.cleaned_data["label"],
                started_by=request.user,
            )
        except services.ReportsUnavailable as error:
            messages.error(request, str(error))
            return _back(request, run)
        messages.success(
            request,
            _("Rerun started with %(model)s on the same data.") % {"model": rerun.model_name},
        )
        return _back(request, rerun)


class SettingsView(StaffRequiredMixin, View):
    def post(self, request):
        form = SettingsForm(request.POST, instance=ReportSettings.load())
        if form.is_valid():
            form.save()
            messages.success(request, _("Report settings saved."))
        else:
            messages.error(request, _("The settings could not be saved."))
        return _back(request)


class EstimateView(StaffRequiredMixin, View):
    """What the run the form describes would cost, for the form to show."""

    def get(self, request):
        season = _current_season()
        model = request.GET.get("model")
        effort = request.GET.get("effort")
        scope = request.GET.get("scope", ACTIVE)
        context = {}
        if catalog.get(model) is None or effort not in catalog.EFFORTS:
            context["problem"] = _("Choose a model and an effort.")
            return render(request, "reports/partials/estimate.html", context)
        if scope == BRIEFING:
            rosters = []
        elif scope == CHOSEN:
            rosters = Roster.objects.filter(season=season, pk__in=request.GET.getlist("rosters"))
        else:
            rosters = Roster.objects.filter(season=season, is_active=True)
        try:
            built = inputs.build(season, rosters)
        except inputs.NoReportData as error:
            context["problem"] = str(error)
        else:
            own = [entry.own_input for entry in built.rosters]
            context.update(
                estimate=costs.estimate_run(model, effort, built.shared_input, own),
                tokens=costs.estimate_tokens(built.shared_input),
                rosters=len(own),
                players=len(built.player_refs),
            )
        return render(request, "reports/partials/estimate.html", context)


class RunProgressView(StaffRequiredMixin, View):
    """A run's progress while it works; reloads the page once it is done."""

    def get(self, request, run_id):
        run = get_object_or_404(ReportRun, pk=run_id)
        if not run.is_active:
            response = HttpResponse(status=200)
            response["HX-Refresh"] = "true"
            return response
        statuses = list(run.reports.values_list("status", flat=True))
        done = sum(
            1 for status in statuses if status in (Report.Status.SUCCEEDED, Report.Status.FAILED)
        )
        return render(
            request,
            "reports/partials/progress.html",
            {"run": run, "done": done, "total": len(statuses)},
        )
