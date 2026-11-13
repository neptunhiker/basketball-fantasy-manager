"""Small formatting helpers for the trade plan screens."""

import datetime as dt
from decimal import Decimal

from django import template
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

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


@register.filter
def days_away(day):
    """How far `day` is from today: `Today`, `Tomorrow`, `in 10 days`, `3 days ago`."""
    if not day:
        return ""
    days = (day - timezone.localdate()).days
    if days == 0:
        return _("Today")
    if days == 1:
        return _("Tomorrow")
    if days == -1:
        return _("Yesterday")
    if days > 0:
        return ngettext("in %(count)d day", "in %(count)d days", days) % {"count": days}
    return ngettext("%(count)d day ago", "%(count)d days ago", -days) % {"count": -days}


@register.filter
def short_name(player):
    """`D. Mitchell` for Donovan Mitchell: enough to tell players apart in a line.

    A first name that is already initials (AJ, CJ) stays as it is, and a
    player without one is his last name alone.
    """
    if player is None:
        return ""
    first, last = (player.first_name or "").strip(), (player.last_name or "").strip()
    if not first:
        return last
    if len(first) <= 3 and first.isupper():
        return f"{first} {last}"
    return f"{first[0]}. {last}"


@register.simple_tag
def plan_player(result, pk):
    """The player behind an id in a plan result."""
    return result.players.get(pk)
