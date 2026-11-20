"""Template filters for reading player figures on the screens."""

from django import template
from django.template.defaultfilters import floatformat
from django.utils import dateformat
from django.utils.formats import get_format
from django.utils.translation import gettext, ngettext

from apps.nba.models import MIN_GAMES_FOR_UPDATE
from apps.nba.stats import hotness_reading as _hotness_reading

# What an empty figure shows, spelled out so it is not mistaken for a hyphen.
DASH = "\N{EN DASH}"

register = template.Library()


@register.simple_tag
def min_games_for_update():
    """The games a player needs before a salary update reprices him.

    `{% min_games_for_update as needed %}`
    """
    return MIN_GAMES_FOR_UPDATE


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


@register.filter
def scatter_points(chart):
    """What the scatter chart's details panel shows, per dot, ready for JSON.

    Formatted here exactly as the template used to format each dot's data
    attributes, so the panel reads the same in either language.
    """
    if not chart:
        return []
    return [
        {
            "label": dot["label"],
            "x": dot["x"],
            "y": dot["y"],
            "anchor": dot["label_anchor"],
            "color": dot.get("color") or "#a1a1aa",
            "ppg": floatformat(dot["x_value"], "2g"),
            "salary": floatformat(dot["y_value"], "2g"),
            "expectedSalary": floatformat(dot.get("expected_salary"), "2g"),
            "difference": floatformat(dot.get("difference"), "2g"),
            "totalFp": floatformat(dot.get("total_fp"), "2g"),
            "gamesPlayed": dot.get("games_played") or DASH,
            "hotness": dot.get("hotness") or DASH,
            "detailUrl": dot.get("detail_url", ""),
        }
        for dot in chart["dots"]
    ]
