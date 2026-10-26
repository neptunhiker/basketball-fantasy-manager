"""Template filters for reading player figures on the screens."""

from django import template
from django.utils import dateformat
from django.utils.formats import get_format
from django.utils.translation import gettext, ngettext

from apps.nba.stats import hotness_reading as _hotness_reading

register = template.Library()


@register.filter
def hotness_reading(score):
    """`"3/7"|hotness_reading` -> {"increases": 3, "comparisons": 7, "label": "steady"}."""
    return _hotness_reading(score)


@register.filter
def short_month_day(value):
    """`2 Nov` / `2. Nov`: the locale's MONTH_DAY_FORMAT with the month cut short."""
    if not value:
        return ""
    return dateformat.format(value, get_format("MONTH_DAY_FORMAT").replace("F", "M"))


@register.filter
def days_until(days):
    """`player.return_in_days|days_until` -> "today", "in 3 days", "2 days ago"."""
    if days is None:
        return ""
    if days == 0:
        return gettext("today")
    if days == 1:
        return gettext("tomorrow")
    if days == -1:
        return gettext("yesterday")
    if days > 0:
        return ngettext("in %(count)d day", "in %(count)d days", days) % {"count": days}
    return ngettext("%(count)d day ago", "%(count)d days ago", -days) % {"count": -days}
