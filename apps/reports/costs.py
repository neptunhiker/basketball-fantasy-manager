"""What a report cost, what a run will cost, and what this month has cost.

Every input token is billed at exactly one rate: written to the cache, read
from it, or neither. Reasoning tokens are part of the output tokens.
"""

import math
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from . import catalog

MILLION = Decimal(1_000_000)
CENT_FRACTION = Decimal("0.000001")
# Tables of numbers and pipes come out at about two characters a token: the
# phase 0 probe sent 120 table rows (10,200 characters) as 5,400 tokens. An
# estimate errs high rather than low.
CHARS_PER_TOKEN = 2.2
# What an answer is expected to come to, reasoning included, by effort. From
# the plan's estimate; replaced by real averages once there are runs to learn from.
EXPECTED_OUTPUT = {catalog.LOW: 3_000, catalog.MEDIUM: 6_000, catalog.HIGH: 12_000}


def usage_cost(model_id, *, input_tokens, cached_tokens=0, cache_write_tokens=0, output_tokens=0):
    """Dollars for one call's usage, to a millionth of a dollar.

    `input_tokens` counts every input token, cached and written ones included,
    as OpenAI reports it. Raises `ValueError` for a model not in the catalog:
    a cost has to be worked out while its price is still known.
    """
    model = catalog.get(model_id)
    if model is None:
        raise ValueError(f"No price for model {model_id!r}.")
    fresh = max(input_tokens - cached_tokens - cache_write_tokens, 0)
    dollars = (
        fresh * model.input
        + cached_tokens * model.cached_input
        + cache_write_tokens * model.input * catalog.CACHE_WRITE_FACTOR
        + output_tokens * model.output
    ) / MILLION
    return dollars.quantize(CENT_FRACTION, rounding=ROUND_HALF_UP)


def estimate_tokens(text):
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def estimate_run(model_id, effort, shared_input, own_inputs):
    """Expected dollars for a run: the briefing writes the shared input to the
    cache, and so does the first roster report -- its answer schema differs
    from the briefing's and OpenAI puts the schema ahead of our text, so it
    cannot reuse the briefing's entry (seen in the first real run). Every
    later roster report reads it back and adds its own part.

    `own_inputs` holds one text per roster report (none for briefing only).
    """
    shared = estimate_tokens(shared_input)
    output = EXPECTED_OUTPUT[effort]
    total = usage_cost(
        model_id, input_tokens=shared, cache_write_tokens=shared, output_tokens=output
    )
    for index, own in enumerate(own_inputs):
        written, read = (shared, 0) if index == 0 else (0, shared)
        total += usage_cost(
            model_id,
            input_tokens=shared + estimate_tokens(own),
            cached_tokens=read,
            cache_write_tokens=written,
            output_tokens=output,
        )
    return total


def month_start(now=None):
    """Midnight on the first of this month, local time."""
    now = timezone.localtime(now or timezone.now())
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def month_to_date(now=None):
    """What every report finished this month has cost, in dollars."""
    from .models import Report

    total = Report.objects.filter(finished_at__gte=month_start(now)).aggregate(
        total=Sum("cost_usd")
    )["total"]
    return total or Decimal(0)


def monthly_limit():
    return Decimal(str(settings.AI_BRIEFING_MONTHLY_LIMIT_USD))


def limit_reached(now=None):
    return month_to_date(now) >= monthly_limit()
