"""Money formatting for templates.

Salaries and budgets are eight- and nine-figure numbers. Written out in full
they are unreadable at a glance -- `60000000` and `6000000` differ by one
character -- so every amount in the UI goes through `millions`.
"""

from decimal import Decimal, InvalidOperation

from django import template
from django.utils.formats import number_format

register = template.Library()

MILLION = Decimal("1000000")
MISSING = "\u2013"  # en dash, for an amount nobody has recorded yet


def _signed(amount, currency, suffix, **format_kwargs):
    """`$60.00M`, and `-$6.55M` rather than `$-6.55M`.

    The sign belongs outside the currency symbol: a minus wedged between the
    dollar and the digits reads as part of the number instead of as its
    direction, which matters in a column of cash flows.
    """
    sign = "-" if amount < 0 else ""
    formatted = number_format(abs(amount), **format_kwargs)
    return f"{sign}{currency}{formatted}{suffix}"


@register.filter
def millions(value, currency="$"):
    """`60000000` -> `$60.00M`.

    Two decimals means the display is rounded to the nearest 10,000, so two
    amounts that look equal can differ slightly. Nothing is ever decided on the
    rendered string -- affordability and cash are compared in the database on
    the exact values -- but that is why the decisive figures also carry the
    exact amount in a `title`.
    """
    if value is None or value == "":
        return MISSING
    try:
        amount = Decimal(value) / MILLION
    except (TypeError, ValueError, InvalidOperation):
        return MISSING

    return _signed(amount, currency, "M", decimal_pos=2)


@register.filter
def exact(value, currency="$"):
    """The full amount, thousands grouped -- for `title` attributes.

    Cents are shown only when there are any. Salaries are whole amounts and
    reading `60,000,000.00` is no easier than reading the digits it replaces,
    but a derived figure like an average salary does have a remainder -- and a
    filter called `exact` must not quietly drop it.
    """
    if value is None or value == "":
        return ""
    try:
        amount = Decimal(value)
    except (TypeError, ValueError, InvalidOperation):
        return ""

    places = 0 if amount == amount.to_integral_value() else 2
    return _signed(amount, currency, "", decimal_pos=places, force_grouping=True)
