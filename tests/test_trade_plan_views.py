"""The trade plan screens: who may see what, and what each action leaves behind."""

import datetime as dt
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.fantasy import services
from apps.fantasy.models import PlanStep, Roster, RosterPlayer, Season, TradePlan
from apps.fantasy.simulator import next_processing_day
from apps.nba.models import Player

M = Decimal("1000000")


@pytest.fixture
def season(db):
    now = timezone.now()
    return Season.objects.create(
        label="2026-27",
        starts_on=timezone.localdate() - dt.timedelta(days=25),
        ends_on=timezone.localdate() + dt.timedelta(days=150),
        is_current=True,
        signings_close_at=now - dt.timedelta(days=26),
        trade_market_opens_at=now - dt.timedelta(days=5),
        trade_market_closes_at=now + dt.timedelta(days=100),
    )


@pytest.fixture
def players(db):
    return {
        position: [
            Player.objects.create(
                first_name=f"{position}{i}",
                last_name="Player",
                position=position,
                current_salary=2 * M,
            )
            for i in range(8)
        ]
        for position in "GFC"
    }


@pytest.fixture
def roster(season, manager, players):
    roster = Roster.objects.create(
        manager=manager, season=season, name="Bulla Ballers", cash=3 * M, trades_available=2
    )
    for position, count in (("G", 5), ("F", 7), ("C", 3)):
        for player in players[position][:count]:
            RosterPlayer.objects.create(
                roster=roster, player=player, added_at=timezone.now() - dt.timedelta(days=30)
            )
    return roster


@pytest.fixture
def signed_in(client, user, password):
    client.login(email=user.email, password=password)
    return client


@pytest.fixture
def plan(roster):
    return TradePlan.objects.create(roster=roster, name="Variant 1")


@pytest.fixture
def day():
    return next_processing_day() + dt.timedelta(days=1)


def add_trade(plan, day, out, in_):
    return services.add_plan_step(plan, day, player_out=out, player_in=in_)


def detail_url(plan):
    return reverse("fantasy:plan-detail", args=[plan.roster_id, plan.pk])


# --- who sees what -----------------------------------------------------------


def test_signed_out_is_sent_to_sign_in(client, plan):
    response = client.get(detail_url(plan))
    assert response.status_code == 302


def test_someone_elses_plan_is_a_404(client, plan, other_manager):
    client.force_login(other_manager.user)
    assert client.get(detail_url(plan)).status_code == 404
    list_url = reverse("fantasy:plan-list", args=[plan.roster_id])
    assert client.get(list_url).status_code == 404


def test_a_plan_under_another_roster_url_is_a_404(signed_in, plan, season, manager):
    other = Roster.objects.create(manager=manager, season=season, name="Second")
    url = reverse("fantasy:plan-detail", args=[other.pk, plan.pk])
    assert signed_in.get(url).status_code == 404


# --- the list ----------------------------------------------------------------


def test_the_list_compares_every_variant(signed_in, plan, roster, players, day):
    add_trade(plan, day, players["G"][0], players["G"][7])
    broken = TradePlan.objects.create(roster=roster, name="Too greedy")
    add_trade(broken, day, players["G"][0], players["F"][7])  # leaves four Guards
    response = signed_in.get(reverse("fantasy:plan-list", args=[roster.pk]))
    assert response.status_code == 200
    content = response.content.decode()
    assert "Variant 1" in content and "Too greedy" in content
    assert "Works" in content
    assert "Breaks on" in content


def test_an_empty_list_offers_the_first_variant(signed_in, roster):
    response = signed_in.get(reverse("fantasy:plan-list", args=[roster.pk]))
    assert "Create your first variant" in response.content.decode()


def test_the_roster_page_links_to_the_plans(signed_in, roster):
    response = signed_in.get(reverse("fantasy:roster-build", args=[roster.pk]))
    assert reverse("fantasy:plan-list", args=[roster.pk]) in response.content.decode()


# --- the plan ----------------------------------------------------------------


def test_the_plan_page_shows_each_day_and_move(signed_in, plan, players, day):
    add_trade(plan, day, players["G"][0], players["G"][7])
    response = signed_in.get(detail_url(plan))
    content = response.content.decode()
    assert response.status_code == 200
    assert players["G"][0].full_name in content
    assert players["G"][7].full_name in content
    assert "Day by day" in content


def test_an_empty_plan_says_so(signed_in, plan):
    assert "Nothing planned yet" in signed_in.get(detail_url(plan)).content.decode()


def test_the_page_costs_the_same_queries_for_more_moves(signed_in, plan, players, day):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    add_trade(plan, day, players["G"][0], players["G"][7])
    signed_in.get(detail_url(plan))  # warm up: grants paid, sessions touched
    with CaptureQueriesContext(connection) as one:
        signed_in.get(detail_url(plan))
    for index in range(3):
        add_trade(
            plan, day + dt.timedelta(days=index), players["C"][index], players["C"][3 + index]
        )
    with CaptureQueriesContext(connection) as four:
        signed_in.get(detail_url(plan))
    assert len(four) == len(one)


# --- making and changing plans -----------------------------------------------


def test_creating_a_variant_opens_it(signed_in, roster):
    response = signed_in.post(
        reverse("fantasy:plan-create", args=[roster.pk]), {"name": "Sunday"}, HTTP_HX_REQUEST="true"
    )
    plan = TradePlan.objects.get(name="Sunday")
    assert response.status_code == 204
    assert response["HX-Redirect"] == detail_url(plan)


def test_the_prompt_suggests_the_next_free_name(signed_in, plan, roster):
    response = signed_in.get(reverse("fantasy:plan-create", args=[roster.pk]))
    assert 'value="Variant 2"' in response.content.decode()


def test_a_name_already_taken_is_refused(signed_in, plan, roster):
    response = signed_in.post(
        reverse("fantasy:plan-create", args=[roster.pk]),
        {"name": "variant 1"},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    assert "already has a plan called" in response.content.decode()
    assert TradePlan.objects.count() == 1


def test_renaming(signed_in, plan):
    signed_in.post(
        reverse("fantasy:plan-rename", args=[plan.roster_id, plan.pk]),
        {"name": "Plan B"},
        HTTP_HX_REQUEST="true",
    )
    plan.refresh_from_db()
    assert plan.name == "Plan B"


def test_duplicating_copies_the_moves_and_opens_the_copy(signed_in, plan, players, day):
    step = add_trade(plan, day, players["G"][0], players["G"][7])
    response = signed_in.post(
        reverse("fantasy:plan-duplicate", args=[plan.roster_id, plan.pk]), HTTP_HX_REQUEST="true"
    )
    copy = TradePlan.objects.get(name="Variant 1 (copy)")
    assert response["HX-Redirect"] == detail_url(copy)
    (copied,) = copy.steps.all()
    assert (copied.player_out, copied.player_in, copied.processing_date) == (
        step.player_out,
        step.player_in,
        step.processing_date,
    )
    # Kept, so a move already made still counts as done in the copy.
    assert copied.created_at == step.created_at


def test_a_second_copy_gets_its_own_name(plan):
    services.duplicate_plan(plan)
    assert services.duplicate_plan(plan).name == "Variant 1 (copy 2)"


def test_deleting_a_variant_leaves_the_roster_alone(signed_in, plan, roster):
    response = signed_in.post(
        reverse("fantasy:plan-delete", args=[roster.pk, plan.pk]), HTTP_HX_REQUEST="true"
    )
    assert response["HX-Redirect"] == reverse("fantasy:plan-list", args=[roster.pk])
    assert not TradePlan.objects.exists()
    assert Roster.objects.filter(pk=roster.pk).exists()


def test_removing_a_move_returns_the_new_plan(signed_in, plan, players, day):
    step = add_trade(plan, day, players["G"][0], players["G"][7])
    response = signed_in.post(
        reverse("fantasy:plan-step-delete", args=[plan.roster_id, plan.pk, step.pk]),
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    assert 'id="plan-body"' in response.content.decode()
    assert not PlanStep.objects.exists()


def test_a_move_cannot_be_planned_for_a_day_already_processed(plan, players):
    yesterday = timezone.localdate() - dt.timedelta(days=1)
    with pytest.raises(services.ValidationError):
        add_trade(plan, yesterday, players["G"][0], players["G"][7])
