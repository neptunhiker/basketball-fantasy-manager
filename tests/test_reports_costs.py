"""What AI reports cost: `apps.reports.costs` and the model catalog."""

from decimal import Decimal

import pytest

from apps.reports import catalog, costs
from apps.reports.models import Report
from tests.reports_factories import at, make_report, make_run, make_season


@pytest.mark.parametrize(
    ("model_id", "expected"),
    [
        # 1M fresh input + 1M output at list prices.
        ("gpt-6-luna", Decimal("0.60")),
        ("gpt-6.1-terra", Decimal("6.00")),
        ("gpt-6.1-sol", Decimal("12.00")),
        ("gpt-6-astra", Decimal("60.00")),
    ],
)
def test_fresh_input_and_output_at_list_price(model_id, expected):
    cost = costs.usage_cost(model_id, input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost == expected


def test_each_input_token_is_billed_at_one_rate():
    # Sol: 1M in total, of which 600K read from the cache and 300K written to it.
    cost = costs.usage_cost(
        "gpt-6.1-sol",
        input_tokens=1_000_000,
        cached_tokens=600_000,
        cache_write_tokens=300_000,
    )
    fresh = Decimal("0.1") * 2  # 100K at $2/M
    cached = Decimal("0.6") * Decimal("0.10")
    written = Decimal("0.3") * 2 * Decimal("1.25")
    assert cost == fresh + cached + written


def test_phase_zero_probe_figures():
    """The real usage of the phase 0 Sol call that read the cache."""
    cost = costs.usage_cost("gpt-6.1-sol", input_tokens=5411, cached_tokens=5408, output_tokens=173)
    assert cost == Decimal("0.002277")  # 0.0022768, rounded


def test_unknown_model_has_no_price():
    with pytest.raises(ValueError, match="gpt-4o"):
        costs.usage_cost("gpt-4o", input_tokens=10)


def test_estimate_grows_with_rosters_and_effort():
    shared = "x" * 44_000  # about 20K tokens
    alone = costs.estimate_run("gpt-6.1-sol", catalog.MEDIUM, shared, [])
    with_rosters = costs.estimate_run("gpt-6.1-sol", catalog.MEDIUM, shared, ["y" * 6600] * 10)
    harder = costs.estimate_run("gpt-6.1-sol", catalog.HIGH, shared, ["y" * 6600] * 10)
    assert 0 < alone < with_rosters < harder
    # The plan's figure for Sol: about a dollar for a briefing and ten rosters.
    assert Decimal("0.5") < with_rosters < Decimal("1.5")


def test_catalog_names_and_defaults():
    assert catalog.DEFAULT_MODEL == "gpt-6-luna"
    assert catalog.get("gpt-6-luna").name == "GPT-6 Luna"
    assert catalog.get("gpt-4o") is None
    assert catalog.display_name("gpt-4o") == "gpt-4o"
    assert [model_id for model_id, _ in catalog.choices()] == [
        "gpt-6-luna",
        "gpt-6.1-terra",
        "gpt-6.1-sol",
        "gpt-6-astra",
    ]


@pytest.mark.django_db
def test_month_to_date_counts_only_this_month(settings):
    season = make_season()
    run = make_run(season)
    make_report(run, cost_usd=Decimal("1.50"), finished_at=at(11, 3))
    other = make_run(season)
    make_report(other, cost_usd=Decimal("2.25"), finished_at=at(11, 14))
    last_month = make_run(season)
    make_report(last_month, cost_usd=Decimal("9.00"), finished_at=at(10, 31, 23))
    # Not finished yet: nothing billed.
    make_report(make_run(season), cost_usd=Decimal("0"))

    now = at(11, 15)
    assert costs.month_start(now) == at(11, 1, 0)
    assert costs.month_to_date(now) == Decimal("3.75")

    settings.AI_BRIEFING_MONTHLY_LIMIT_USD = "3.75"
    assert costs.limit_reached(now)
    settings.AI_BRIEFING_MONTHLY_LIMIT_USD = "20"
    assert not costs.limit_reached(now)
    assert Report.objects.count() == 4
