import datetime as dt

from django.conf import settings
from django.utils import timezone

from .models import RequestMetric


def prune_request_metrics(now=None):
    """Delete metrics older than `PERF_RETENTION_DAYS`; returns how many went.

    Run daily by a Django-Q schedule, so the table holds a rolling window
    rather than growing for as long as the app runs.
    """
    cutoff = (now or timezone.now()) - dt.timedelta(days=settings.PERF_RETENTION_DAYS)
    deleted, _ = RequestMetric.objects.filter(recorded_at__lt=cutoff).delete()
    return deleted
