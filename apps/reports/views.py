"""The Reports page: published AI trade reports, and the staff controls for runs."""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.views.generic import TemplateView

from apps.fantasy.models import Season

from . import pages
from .models import ReportRun


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
        return context
