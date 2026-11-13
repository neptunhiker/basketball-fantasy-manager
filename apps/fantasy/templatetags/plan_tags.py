"""Small formatting helpers for the trade plan screens."""

import datetime as dt
from decimal import Decimal

from django import template
from django.utils import timezone
from django.utils.translation import gettext as _

register = template.Library()


MINUS = "\u2212"
EN_DASH = "\u2013"
THIN_SPACE = "\u202f"


@register.filter
def signed_points(value):
    """Projected points as a whole signed number: +42, minus 17, ±0, a dash when unknown.

    A true minus sign and thousands grouped by a narrow space, so a column of
    them lines up and reads the same in English and German.
    """
    if value is None:
        return EN_DASH
    rounded = Decimal(value).quantize(Decimal("1"))
    if rounded == 0:
        return "±0"
    sign = "+" if rounded > 0 else MINUS
    return f"{sign}{abs(rounded):,}".replace(",", THIN_SPACE)


@register.filter
def relative_day(day):
    """`Today`, `Tomorrow`, or "" for any other day."""
    if not day:
        return ""
    today = timezone.localdate()
    if day == today:
        return _("Today")
    if day == today + dt.timedelta(days=1):
        return _("Tomorrow")
    return ""


@register.simple_tag
def plan_player(result, pk):
    """The player behind an id in a plan result."""
    return result.players.get(pk)
