"""Starting, running, retrying and publishing AI report runs.

A run is never one background task: the briefing is one, and each roster
report is one, queued once the briefing is done so they find the shared input
already cached. A task that is retried repeats only its own report, and never
one that already succeeded -- Django-Q retries a task whose worker died, and
paying twice for an answer we already have would be the expensive kind of bug.

`run_report` holds no database transaction or lock while it waits for the
model: a call can take minutes.
"""

import logging
import time
import uuid
from functools import partial

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _
from django_q.tasks import async_task

from apps.fantasy.models import Roster

from . import ai, catalog, costs, inputs, prompts, schemas
from .models import RecommendedTrade, Report, ReportRun

logger = logging.getLogger(__name__)

TASK = "apps.reports.tasks.generate_report"
MAX_TRADES = 2


class ReportsUnavailable(Exception):
    """A run cannot be started (now). The message says why, for the screen."""


def unavailable_reason(now=None):
    """Why no run can be started right now, or "" when one can."""
    if not settings.AI_BRIEFING_API_KEY:
        return _("AI reports are switched off: no OpenAI API key is set.")
    if costs.limit_reached(now):
        return _("This month's spending limit of $%(limit)s has been reached.") % {
            "limit": costs.monthly_limit()
        }
    return ""


def _check(model, effort, now=None):
    if catalog.get(model) is None:
        raise ReportsUnavailable(_("%(model)s is not an approved model.") % {"model": model})
    if effort not in catalog.EFFORTS:
        raise ReportsUnavailable(_("Unknown reasoning effort: %(effort)s.") % {"effort": effort})
    if reason := unavailable_reason(now):
        raise ReportsUnavailable(reason)


def start_run(
    season,
    *,
    model,
    effort,
    trigger,
    started_by=None,
    label="",
    briefing_only=False,
    rosters=None,
    now=None,
):
    """Build the inputs, store the run and its reports, and queue the briefing.

    `rosters` defaults to the season's active rosters. Raises
    `ReportsUnavailable` when no run can start.
    """
    _check(model, effort, now)
    if briefing_only:
        rosters = []
    elif rosters is None:
        rosters = Roster.objects.filter(season=season, is_active=True)
    try:
        built = inputs.build(season, rosters, now=now)
    except inputs.NoReportData as error:
        raise ReportsUnavailable(str(error)) from error

    with transaction.atomic():
        run = ReportRun.objects.create(
            season=season,
            trigger=trigger,
            started_by=started_by,
            model=model,
            effort=effort,
            label=label.strip(),
            prompt_version=prompts.VERSION,
            scope=ReportRun.Scope.BRIEFING_ONLY if briefing_only else ReportRun.Scope.FULL,
            data_as_of=built.data_as_of,
            shared_input=built.shared_input,
            player_refs=built.player_refs,
        )
        briefing = Report.objects.create(run=run, kind=Report.Kind.BRIEFING)
        Report.objects.bulk_create(
            Report(
                run=run,
                kind=Report.Kind.ROSTER,
                roster=entry.roster,
                own_input=entry.own_input,
                shortlist=entry.shortlist,
            )
            for entry in built.rosters
        )
        transaction.on_commit(partial(enqueue, briefing.pk))
    return run


def rerun_with_model(run, *, model, effort, started_by=None, label="", now=None):
    """Put `run`'s exact inputs to another model (or effort) as a new run.

    The way to compare models fairly: same data, same shortlists. Rosters
    deleted since are left out.
    """
    _check(model, effort, now)
    with transaction.atomic():
        rerun = ReportRun.objects.create(
            season=run.season,
            trigger=ReportRun.Trigger.RERUN,
            started_by=started_by,
            source_run=run,
            model=model,
            effort=effort,
            label=label.strip(),
            prompt_version=prompts.VERSION,
            scope=run.scope,
            data_as_of=run.data_as_of,
            shared_input=run.shared_input,
            player_refs=run.player_refs,
        )
        briefing = Report.objects.create(run=rerun, kind=Report.Kind.BRIEFING)
        Report.objects.bulk_create(
            Report(
                run=rerun,
                kind=Report.Kind.ROSTER,
                roster_id=report.roster_id,
                own_input=report.own_input,
                shortlist=report.shortlist,
            )
            for report in run.reports.filter(kind=Report.Kind.ROSTER, roster__isnull=False)
        )
        transaction.on_commit(partial(enqueue, briefing.pk))
    return rerun


def retry_failed(run, now=None):
    """Queue the run's failed reports again. Returns how many."""
    failed = list(run.reports.filter(status=Report.Status.FAILED))
    if not failed:
        return 0
    if reason := unavailable_reason(now):
        raise ReportsUnavailable(reason)
    with transaction.atomic():
        Report.objects.filter(pk__in=[report.pk for report in failed]).update(
            status=Report.Status.PENDING, error=""
        )
        ReportRun.objects.filter(pk=run.pk).update(
            status=ReportRun.Status.RUNNING, finished_at=None
        )
        briefing = next((report for report in failed if report.is_briefing), None)
        # A retried briefing queues the pending roster reports itself, after it.
        for report in [briefing] if briefing else failed:
            transaction.on_commit(partial(enqueue, report.pk))
    run.refresh_from_db()
    return len(failed)


def publish(run, user=None, now=None):
    """Make the run visible to players. Only a finished run with an answer."""
    if run.is_active:
        raise ReportsUnavailable(_("A run can only be published once it has finished."))
    if not run.reports.filter(status=Report.Status.SUCCEEDED).exists():
        raise ReportsUnavailable(_("This run has no report to show."))
    run.is_published = True
    run.published_at = now or timezone.now()
    run.published_by = user
    run.save(update_fields=["is_published", "published_at", "published_by", "updated_at"])


def unpublish(run):
    run.is_published = False
    run.save(update_fields=["is_published", "updated_at"])


def enqueue(report_id):
    async_task(TASK, str(report_id), group="reports")


# --- doing the work -----------------------------------------------------------


def run_report(report_id):
    """Generate one report, store the answer, and move the run along.

    Skips a report that already succeeded. Returns the report, or None when it
    no longer exists.
    """
    with transaction.atomic():
        report = (
            Report.objects.select_for_update().select_related("run").filter(pk=report_id).first()
        )
        if report is None:
            return None
        if report.status == Report.Status.SUCCEEDED:
            return report
        report.status = Report.Status.RUNNING
        report.attempts += 1
        report.started_at = timezone.now()
        report.error = ""
        report.save(update_fields=["status", "attempts", "started_at", "error", "updated_at"])
        ReportRun.objects.filter(pk=report.run_id, status=ReportRun.Status.QUEUED).update(
            status=ReportRun.Status.RUNNING
        )

    run = report.run
    started = time.monotonic()
    try:
        result = ai.generate(
            model=run.model,
            effort=run.effort,
            preamble=prompts.COMMON,
            shared_input=run.shared_input,
            task=_task(report),
            schema=schemas.Briefing if report.is_briefing else schemas.RosterAdvice,
        )
    except ai.AiError as error:
        _fail(report, str(error), error.usage, started)
    except Exception as error:  # a report must never be left "running"
        logger.exception("AI report %s failed", report.pk)
        _fail(report, f"{type(error).__name__}: {error}", None, started)
    else:
        _succeed(report, result, started)

    if report.is_briefing:
        pending = run.reports.filter(kind=Report.Kind.ROSTER, status=Report.Status.PENDING)
        for report_id in pending.values_list("pk", flat=True):
            transaction.on_commit(partial(enqueue, report_id))
    _finish_run(run.pk)
    return report


def _task(report):
    if report.is_briefing:
        return prompts.BRIEFING_TASK
    return f"{prompts.ROSTER_TASK}\n\n{report.own_input}"


def _record_usage(report, usage, started):
    usage = usage or ai.Usage()
    report.input_tokens = usage.input_tokens
    report.cached_tokens = usage.cached_tokens
    report.cache_write_tokens = usage.cache_write_tokens
    report.output_tokens = usage.output_tokens
    report.reasoning_tokens = usage.reasoning_tokens
    try:
        report.cost_usd = costs.usage_cost(
            report.run.model,
            input_tokens=usage.input_tokens,
            cached_tokens=usage.cached_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            output_tokens=usage.output_tokens,
        )
    except ValueError:
        report.cost_usd = 0
    report.duration_ms = round((time.monotonic() - started) * 1000)
    report.finished_at = timezone.now()


USAGE_FIELDS = [
    "input_tokens",
    "cached_tokens",
    "cache_write_tokens",
    "output_tokens",
    "reasoning_tokens",
    "cost_usd",
    "duration_ms",
    "finished_at",
]


def _fail(report, message, usage, started):
    _record_usage(report, usage, started)
    report.status = Report.Status.FAILED
    report.error = message[:2000]
    report.save(update_fields=[*USAGE_FIELDS, "status", "error", "updated_at"])


def _succeed(report, result, started):
    _record_usage(report, result.usage, started)
    refs = report.run.player_refs
    if report.is_briefing:
        output, notes = _clean_briefing(result.parsed, refs)
        picks = []
    else:
        output, picks, notes = _clean_roster(result.parsed, report, refs)
    with transaction.atomic():
        report.output = output
        report.response_id = result.response_id or ""
        report.status = Report.Status.SUCCEEDED
        report.error = " ".join(notes)
        report.save(
            update_fields=[*USAGE_FIELDS, "output", "response_id", "status", "error", "updated_at"]
        )
        report.trades.all().delete()
        by_ref = {trade["ref"]: trade for trade in report.shortlist}
        RecommendedTrade.objects.bulk_create(
            RecommendedTrade(
                report=report,
                rank=rank,
                trade_ref=pick.trade_id,
                player_out_id=uuid.UUID(by_ref[pick.trade_id]["out"]),
                player_in_id=uuid.UUID(by_ref[pick.trade_id]["in"]),
                salary_out=by_ref[pick.trade_id]["salary_out"],
                salary_in=by_ref[pick.trade_id]["salary_in"],
                confidence=pick.confidence,
                reasoning=pick.reasoning,
            )
            for rank, pick in enumerate(picks, start=1)
        )


def _clean_briefing(briefing, refs):
    """The briefing as stored: every player ref one of ours."""
    notes = []
    items = []
    for item in briefing.items:
        known = [ref for ref in item.player_refs if ref in refs]
        if len(known) < len(item.player_refs):
            notes.append(_dropped_refs(set(item.player_refs) - set(known)))
        items.append({"kind": item.kind, "player_refs": known, "text": item.text.strip()})
    output = {
        "headline": briefing.headline.strip(),
        "summary": briefing.summary.strip(),
        "items": items,
    }
    return output, notes


def _clean_roster(advice, report, refs):
    """The advice as stored, and the trades worth a row: only shortlist trades,
    at most two, each once; a trade decision left without a trade is a hold."""
    notes = []
    offered = {trade["ref"] for trade in report.shortlist}
    picks = []
    for pick in sorted(advice.trades, key=lambda pick: pick.priority):
        if pick.trade_id not in offered:
            notes.append(f"Dropped trade {pick.trade_id}: not on the shortlist.")
            continue
        if pick.trade_id not in {chosen.trade_id for chosen in picks}:
            picks.append(pick)
    picks = picks[:MAX_TRADES]

    decision = advice.decision
    hold_reason = advice.hold_reason.strip()
    if decision == "hold" and picks:
        notes.append("Advised to hold, so the trades it also listed are not shown.")
        picks = []
    if decision != "hold" and not picks:
        notes.append("No usable trade left in the answer: shown as hold.")
        decision = "hold"

    watch = [item for item in advice.watch if item.player_ref in refs]
    if len(watch) < len(advice.watch):
        notes.append(_dropped_refs({item.player_ref for item in advice.watch} - set(refs)))

    output = {
        "headline": advice.headline.strip(),
        "situation": advice.situation.strip(),
        "decision": decision,
        "trades": [
            {
                "trade_id": pick.trade_id,
                "priority": rank,
                "reasoning": pick.reasoning.strip(),
                "confidence": pick.confidence,
            }
            for rank, pick in enumerate(picks, start=1)
        ],
        "hold_reason": hold_reason,
        "watch": [{"player_ref": item.player_ref, "text": item.text.strip()} for item in watch],
        "risks": advice.risks.strip(),
    }
    return output, picks, notes


def _dropped_refs(refs):
    return "Dropped unknown player refs: " + ", ".join(sorted(refs)) + "."


def _finish_run(run_id, now=None):
    """Settle the run's status once every report is done; publish an automatic
    run whose briefing worked, unless it was published (or hidden) before."""
    with transaction.atomic():
        run = ReportRun.objects.select_for_update().get(pk=run_id)
        statuses = list(run.reports.values_list("kind", "status"))
        done = (Report.Status.SUCCEEDED, Report.Status.FAILED)
        if any(status not in done for kind, status in statuses):
            return run
        succeeded = [kind for kind, status in statuses if status == Report.Status.SUCCEEDED]
        if len(succeeded) == len(statuses):
            run.status = ReportRun.Status.SUCCEEDED
        elif succeeded:
            run.status = ReportRun.Status.PARTIAL
        else:
            run.status = ReportRun.Status.FAILED
        run.finished_at = now or timezone.now()
        fields = ["status", "finished_at", "updated_at"]
        if (
            run.trigger == ReportRun.Trigger.SCHEDULED
            and Report.Kind.BRIEFING in succeeded
            and run.published_at is None
        ):
            run.is_published = True
            run.published_at = run.finished_at
            fields += ["is_published", "published_at"]
        run.save(update_fields=fields)
        return run
