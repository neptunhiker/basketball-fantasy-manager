"""Trade plans as stored: what a plan and its steps may hold.

The arithmetic lives in `apps.fantasy.simulator` (test_trade_plan_simulator.py);
these are the rules the database enforces on its own.
"""

import datetime as dt
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.fantasy.models import PlanStep, Roster, Season, TradePlan
from apps.nba.models import Player


@pytest.fixture
def season(db):
    return Season.objects.create(
        label="2026-27",
        starts_on=timezone.localdate() - dt.timedelta(days=20),
        ends_on=timezone.localdate() + dt.timedelta(days=150),
        is_current=True,
    )


@pytest.fixture
def roster(season, manager):
    return Roster.objects.create(manager=manager, season=season, name="Roster 1")


@pytest.fixture
def plan(roster):
    return TradePlan.objects.create(roster=roster, name="Variant 1")


@pytest.fixture
def two_players(db):
    return [
        Player.objects.create(
            first_name="P", last_name=str(i), position="G", current_salary=Decimal("1000000")
        )
        for i in range(2)
    ]


def test_a_plan_name_is_unique_per_roster(plan, roster):
    with pytest.raises(IntegrityError), transaction.atomic():
        TradePlan.objects.create(roster=roster, name="Variant 1")


def test_two_rosters_may_each_have_a_variant_1(plan, season, manager):
    other = Roster.objects.create(manager=manager, season=season, name="Roster 2")
    assert TradePlan.objects.create(roster=other, name="Variant 1")


def test_a_trade_step_needs_both_players(plan, two_players):
    with pytest.raises(IntegrityError), transaction.atomic():
        PlanStep.objects.create(
            plan=plan,
            processing_date=timezone.localdate(),
            kind=PlanStep.Kind.TRADE,
            player_out=two_players[0],
        )


def test_buying_a_trade_names_no_player(plan, two_players):
    with pytest.raises(IntegrityError), transaction.atomic():
        PlanStep.objects.create(
            plan=plan,
            processing_date=timezone.localdate(),
            kind=PlanStep.Kind.BUY_TRADE,
            player_in=two_players[0],
        )
    assert PlanStep.objects.create(
        plan=plan, processing_date=timezone.localdate(), kind=PlanStep.Kind.SELL_TRADE
    )


def test_a_player_cannot_be_swapped_for_himself(plan, two_players):
    with pytest.raises(IntegrityError), transaction.atomic():
        PlanStep.objects.create(
            plan=plan,
            processing_date=timezone.localdate(),
            player_out=two_players[0],
            player_in=two_players[0],
        )


def test_deleting_a_roster_takes_its_plans_and_steps(plan, roster, two_players):
    PlanStep.objects.create(
        plan=plan,
        processing_date=timezone.localdate(),
        player_out=two_players[0],
        player_in=two_players[1],
    )
    roster.delete()
    assert not TradePlan.objects.exists()
    assert not PlanStep.objects.exists()


def test_steps_come_back_in_day_then_written_order(plan, two_players):
    today = timezone.localdate()
    buy = PlanStep.Kind.BUY_TRADE
    later = PlanStep.objects.create(
        plan=plan, processing_date=today + dt.timedelta(days=1), kind=buy
    )
    second = PlanStep.objects.create(plan=plan, processing_date=today, order=1, kind=buy)
    first = PlanStep.objects.create(plan=plan, processing_date=today, order=0, kind=buy)
    assert list(plan.steps.all()) == [first, second, later]


def test_is_shared_follows_the_token(plan):
    assert not plan.is_shared
    plan.share_token = "6f9d1f2e-0d7a-4f4a-9a49-6f1c35d1c2b1"
    assert plan.is_shared
