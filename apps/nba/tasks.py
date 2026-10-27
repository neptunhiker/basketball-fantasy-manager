"""Background tasks run by the Django-Q cluster."""

import logging

from django.conf import settings

from .importer import import_bbde
from .models import ImportRun

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
