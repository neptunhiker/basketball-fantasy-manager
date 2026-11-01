"""Background tasks run by the Django-Q cluster."""

import logging

from django.conf import settings
from django.utils import timezone

from . import schedule
from .importer import import_bbde
from .models import ImportRun, PlayerNews
from .rotowire import sync_news, why_not_now

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
