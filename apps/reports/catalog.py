"""The AI models staff may run reports with, and what they cost.

A fixed list in code rather than a table: which models are approved, and at
what price, is a decision made once and reviewed in a commit, not something to
change from a form. The prices are OpenAI's list prices per million tokens
(standard tier, prompts under 272K tokens), checked on `PRICES_AS_OF`.

Caching, from OpenAI's prompt caching guide for GPT-5.6 and later: a token
written to the cache costs `CACHE_WRITE_FACTOR` times the input price, a token
read from it the cached-input price. Reasoning tokens are billed as output.
"""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from django.utils.translation import gettext_lazy as _

PRICES_AS_OF = dt.date(2026, 11, 15)
PRICES_SOURCE = "https://developers.openai.com/api/docs/pricing"
CACHE_WRITE_FACTOR = Decimal("1.25")

LOW, MEDIUM, HIGH = "low", "medium", "high"
EFFORTS = (LOW, MEDIUM, HIGH)


@dataclass(frozen=True)
class AiModel:
    id: str
    name: str
    blurb: str
    # Dollars per million tokens.
    input: Decimal
    cached_input: Decimal
    output: Decimal


MODELS = (
    AiModel(
        id="gpt-6-luna",
        name="GPT-6 Luna",
        blurb=_("Cheapest. Good for testing the pipeline."),
        input=Decimal("0.10"),
        cached_input=Decimal("0.01"),
        output=Decimal("0.50"),
    ),
    AiModel(
        id="gpt-6.1-terra",
        name="GPT-6.1 Terra",
        blurb=_("Fast and inexpensive, writes well."),
        input=Decimal("1.00"),
        cached_input=Decimal("0.05"),
        output=Decimal("5.00"),
    ),
    AiModel(
        id="gpt-6.1-sol",
        name="GPT-6.1 Sol",
        blurb=_("Near the flagship's judgment at a fifth of its price."),
        input=Decimal("2.00"),
        cached_input=Decimal("0.10"),
        output=Decimal("10.00"),
    ),
    AiModel(
        id="gpt-6-astra",
        name="GPT-6 Astra",
        blurb=_("OpenAI's flagship. Expensive."),
        input=Decimal("10.00"),
        cached_input=Decimal("1.00"),
        output=Decimal("50.00"),
    ),
)
APPROVED = {model.id: model for model in MODELS}
DEFAULT_MODEL = "gpt-6-luna"
DEFAULT_EFFORT = MEDIUM


def get(model_id):
    """The approved model with this id, or None for one no longer approved."""
    return APPROVED.get(model_id)


def display_name(model_id):
    """The model's name for people, or its id when it has left the list."""
    model = get(model_id)
    return model.name if model else model_id


def choices():
    return [(model.id, model.name) for model in MODELS]
