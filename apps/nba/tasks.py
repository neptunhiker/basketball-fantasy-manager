"""Background tasks run by the Django-Q cluster."""

import datetime as dt
import logging

from django.conf import settings
from django.utils import timezone

from . import schedule
from .importer import import_bbde
from .models import ImportRun, PlayerNews
from .rotowire import sync_news, why_not_now
from .services import NbaApiError, sync_injuries

logger = logging.getLogger(__name__)


def scheduled_bbde_import(client=None):
    """The daily basketball.de import, started by a Django-Q schedule.

    The login comes from settings when the task runs, never from the task's
    arguments: Django-Q stores those in the database. A failure is left to
    raise, so it shows up as a failed task as well as a failed `ImportRun`.
    """
    if not (settings.BBDE_USERNAME and settings.BBDE_PASSWORD):
        logger.warning("Scheduled basketball.de import skipped: no login configured.")
        return "skipped: BBDE_USERNAME and BBDE_PASSWORD are not set"
    run = import_bbde(
        settings.BBDE_USERNAME,
        settings.BBDE_PASSWORD,
        source=ImportRun.Source.SCHEDULED,
        client=client,
    )
    return (
        f"{run.pages} pages, {run.rows} players; {run.matched} matched, "
        f"{run.created} created, {run.reactivated} reactivated, "
        f"{run.inactivated} marked inactive."
    )


def why_no_salary_import(now=None):
    """Why the Sunday-evening import should skip now, or None when it may run.

    The first salary update comes with the opening of the trade phase, the same
    moment trade buying opens, so the season's `trade_market_opens_at` is when
    these imports start.
    """
    season = schedule.current_season()
    if season is None:
        return "no current season is set up"
    if season.trade_market_opens_at is None:
        return f"the season {season.label} has no date for trade buying to open"
    now = now or timezone.now()
    if now < season.trade_market_opens_at:
        opens = timezone.localtime(season.trade_market_opens_at)
        return f"salaries first change when trade buying opens, {opens:%d %B %Y %H:%M}"
    if timezone.localdate(now) > season.ends_on:
        return f"the current season {season.label} is over"
    return None


def scheduled_salary_import(client=None):
    """The Sunday 22:15 basketball.de import, started by a Django-Q schedule.

    The official game reprices every Sunday at 22:00; without this run the new
    salaries would wait for Monday's 12:15 import.
    """
    reason = why_no_salary_import()
    if reason:
        logger.info("Scheduled salary import skipped: %s.", reason)
        return f"skipped: {reason}"
    return scheduled_bbde_import(client=client)


def scheduled_rotowire_news_import():
    """The RotoWire news pull at 6:00, 12:00 and 18:00, started by a Django-Q schedule.

    The feed shows its newest 25 items and nothing older, so a pull in which
    every item is new may have missed some in between; that is logged, as the
    sign the schedule should run more often. A failure is left to raise.
    """
    reason = why_not_now()
    if reason:
        logger.info("Scheduled RotoWire news import skipped: %s.", reason)
        return f"skipped: {reason}"
    had_news = PlayerNews.objects.exists()
    result = sync_news()
    if had_news and result.items and result.created == result.items:
        logger.warning(
            "RotoWire news: all %s items were new, some may have been missed.", result.items
        )
    logger.info("RotoWire news imported: %s", result)
    return str(result)


# The injury feed is pulled from a week before the season opens to its end.
INJURY_DAYS_BEFORE_SEASON = 7


def why_no_injury_import(now=None):
    """Why the daily injury import should skip now, or None when it may run."""
    if not settings.RAPID_API_KEY:
        return "RAPID_API_KEY is not set"
    season = schedule.current_season()
    if season is None:
        return "no current season is set up"
    today = timezone.localdate(now or timezone.now())
    if today < season.starts_on - dt.timedelta(days=INJURY_DAYS_BEFORE_SEASON):
        return f"the season {season.label} has not started"
    if today > season.ends_on:
        return f"the current season {season.label} is over"
    return None


def scheduled_injury_import():
    """The daily 12:20 injury import, started by a Django-Q schedule.

    Right after the 12:15 snapshot, so the Sunday 12:30 report reads the day's
    injuries. It counts against the superuser limit: a manual refresh earlier
    that day must not stop it. A refused or failed request is logged and
    returned, never raised -- Django-Q would retry it and spend more requests.
    """
    reason = why_no_injury_import()
    if reason:
        logger.info("Scheduled injury import skipped: %s.", reason)
        return f"skipped: {reason}"
    try:
        summary = sync_injuries(daily_limit=settings.RAPID_API_SUPERUSER_DAILY_LIMIT)
    except NbaApiError as exc:
        logger.warning("Scheduled injury import failed: %s", exc)
        return f"failed: {exc}"
    logger.info("Injuries imported: %s", summary)
    return (
        f"{summary['created']} created, {summary['updated']} updated, "
        f"{summary['recovered']} recovered, {summary['unmatched']} unmatched, "
        f"{summary['ambiguous']} ambiguous."
    )


def scheduled_nba_schedule_load():
    """The yearly schedule loads -- the season's start, after the NBA Cup group
    stage -- started by Django-Q schedules. A failure is left to raise."""
    reason = schedule.why_not_now()
    if reason:
        logger.info("Scheduled NBA schedule load skipped: %s.", reason)
        return f"skipped: {reason}"
    result = schedule.load_season()
    logger.info("NBA schedule loaded: %s", result)
    return str(result)
