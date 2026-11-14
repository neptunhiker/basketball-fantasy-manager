import datetime as dt

from django import template
from django.utils import timezone
from django.utils.formats import number_format
from django.utils.timesince import timesince
from django.utils.translation import gettext as _

from apps.perf import stats

register = template.Library()

# Typography, spelled out so nobody mistakes them for a hyphen: the dash an
# empty cell shows, and the minus sign of a falling figure.
DASH = "\N{EN DASH}"
MINUS = "\N{MINUS SIGN}"


@register.filter
def duration(ms):
    """812.4 -> "812 ms", 1530 -> "1.53 s" (or "1,53 s"); DASH for None."""
    if ms is None:
        return DASH
    if ms < 1000:
        return _("%(value)s ms") % {"value": number_format(round(ms))}
    return _("%(value)s s") % {"value": number_format(ms / 1000, decimal_pos=2)}


@register.filter
def axis_duration(ms):
    """An axis tick: like `duration`, but whole numbers, so 1500 is "1.5 s"."""
    if ms is None:
        return ""
    if ms < 1000:
        return _("%(value)s ms") % {"value": number_format(round(ms))}
    seconds = ms / 1000
    places = 0 if seconds == int(seconds) else 1
    return _("%(value)s s") % {"value": number_format(seconds, decimal_pos=places)}


@register.filter
def signed_percent(change):
    """A `stats.change(...)` dict -> "+12%" / "-8%" (with a real minus sign) / "±0%"."""
    if not change:
        return ""
    if change.get("flat"):
        return _("±0%")
    sign = "+" if change["up"] else MINUS
    return _("%(sign)s%(value)s%%") % {
        "sign": sign,
        "value": number_format(abs(round(change["percent"]))),
    }


@register.filter
def one_decimal(value):
    return DASH if value is None else number_format(value, decimal_pos=1)


@register.filter
def split_keys(value):
    """`RequestMetric.query_keys` ("q,sort") -> ["q", "sort"]."""
    return [key for key in (value or "").split(",") if key]


@register.filter
def speed(ms):
    """A single request's time as "fast", "ok" or "slow", by the p95 thresholds."""
    return stats.status(ms)


@register.filter
def ago(moment):
    """How long ago, in one unit: "just now", "3 minutes ago", "2 hours ago"."""
    if moment is None:
        return DASH
    if timezone.now() - moment < dt.timedelta(minutes=1):
        return _("just now")
    return _("%(time)s ago") % {"time": timesince(moment, depth=1)}
