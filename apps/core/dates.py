"""Dates and times for messages written in Python.

Templates use the `date` filter with a named format. Messages built in views
and services need the same result, in the reader's language -- `strftime`
would print English month names on the German screens.
"""

import datetime as dt

from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _


def local_date(value):
    """`20 October 2026` / `20. Oktober 2026`."""
    if isinstance(value, dt.datetime):
        value = timezone.localtime(value)
    return date_format(value, "DATE_FORMAT")


def local_moment(value):
    """`20 October 2026 at 18:00` / `20. Oktober 2026 um 18:00 Uhr`."""
    value = timezone.localtime(value)
    return _("%(date)s at %(time)s") % {
        "date": date_format(value, "DATE_FORMAT"),
        "time": date_format(value, "TIME_FORMAT"),
    }
