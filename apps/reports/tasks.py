"""Background tasks run by the Django-Q cluster."""

import datetime as dt
import logging

from django.utils import timezone
from django_q.models import Schedule
from django_q.tasks import schedule

from apps.fantasy.models import Season
from apps.nba.models import ImportRun

from . import services
from .models import ReportRun, ReportSettings

logger = logging.getLogger(__name__)

# The Sunday run waits for a scheduled import still running at 12:30, in
# steps of this long, at most this many times -- then it goes with what is in.
IMPORT_WAIT = dt.timedelta(minutes=10)
IMPORT_WAITS = 6
SUNDAY_TASK = "apps.reports.tasks.scheduled_weekly_run"


def generate_report(report_id):
    """One report of a run (`services.run_report`), queued by `services.enqueue`."""
    report = services.run_report(report_id)
    if report is None:
        return "skipped: the report no longer exists"
    return f"{report.get_kind_display()}: {report.status}, ${report.cost_usd}"


def why_no_sunday_run(now=None):
    """Why the Sunday run should not start now, or "" when it may."""
    now = now or timezone.now()
    report_settings = ReportSettings.load()
    if not report_settings.sunday_run_enabled:
        return "the Sunday run is switched off"
    season = Season.objects.filter(is_current=True).first()
    if season is None:
        return "there is no current season"
    if not season.season_open_at <= now <= season.season_close_at:
        return "the season is not running"
    if reason := services.unavailable_reason(now):
        return reason
    # A retried task, or a second schedule, must not pay for the same Sunday twice.
    if ReportRun.objects.filter(
        season=season,
        trigger=ReportRun.Trigger.SCHEDULED,
        created_at__gte=now - dt.timedelta(hours=12),
    ).exists():
        return "today's automatic run has already started"
    return ""


def import_running(now=None):
    """Whether today's scheduled stats import is still running."""
    now = now or timezone.now()
    return ImportRun.objects.filter(
        source=ImportRun.Source.SCHEDULED,
        status=ImportRun.Status.RUNNING,
        started_at__date=timezone.localdate(now),
        started_at__gte=now - dt.timedelta(hours=2),
    ).exists()


def scheduled_weekly_run(waited=0, now=None):
    """The Sunday 12:30 report: the default model and effort, published by
    itself once its briefing is written (`services._finish_run`).

    Waits for a stats import still running, `IMPORT_WAIT` at a time, at most
    `IMPORT_WAITS` times; then runs on the newest data there is, which the
    report's "Data as of" makes plain.
    """
    now = now or timezone.now()
    if reason := why_no_sunday_run(now):
        logger.info("Sunday AI report skipped: %s.", reason)
        return f"skipped: {reason}"
    if import_running(now) and waited < IMPORT_WAITS:
        schedule(
            SUNDAY_TASK,
            waited + 1,
            name=f"Sunday AI report, waiting for the import ({waited + 1})",
            schedule_type=Schedule.ONCE,
            next_run=now + IMPORT_WAIT,
        )
        return f"waiting for the stats import ({waited + 1} of {IMPORT_WAITS})"

    report_settings = ReportSettings.load()
    try:
        run = services.start_run(
            Season.objects.get(is_current=True),
            model=report_settings.default_model,
            effort=report_settings.default_effort,
            trigger=ReportRun.Trigger.SCHEDULED,
            now=now,
        )
    except services.ReportsUnavailable as error:
        logger.warning("Sunday AI report not started: %s", error)
        return f"not started: {error}"
    return f"started run {run.pk} with {run.model} ({run.effort})"
