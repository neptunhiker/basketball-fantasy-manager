"""Background tasks run by the Django-Q cluster."""

import logging

from django.conf import settings

from .importer import import_bbde
from .models import ImportRun, PlayerNews
from .rotowire import sync_news

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


def scheduled_rotowire_news_import():
    """The four-hourly RotoWire news pull, started by a Django-Q schedule.

    The feed shows its newest 25 items and nothing older, so a pull in which
    every item is new may have missed some in between; that is logged, as the
    sign the schedule should run more often. A failure is left to raise.
    """
    if not settings.RAPID_API_KEY:
        logger.warning("Scheduled RotoWire news import skipped: no RapidAPI key configured.")
        return "skipped: RAPID_API_KEY is not set"
    had_news = PlayerNews.objects.exists()
    result = sync_news()
    if had_news and result.items and result.created == result.items:
        logger.warning(
            "RotoWire news: all %s items were new, some may have been missed.", result.items
        )
    logger.info("RotoWire news imported: %s", result)
    return str(result)
