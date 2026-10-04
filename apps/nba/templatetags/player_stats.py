"""Template filters for reading player figures on the screens."""

from django import template

from apps.nba.stats import hotness_reading as _hotness_reading

register = template.Library()


@register.filter
def hotness_reading(score):
    """`"3/7"|hotness_reading` -> {"increases": 3, "comparisons": 7, "label": "steady"}."""
    return _hotness_reading(score)
