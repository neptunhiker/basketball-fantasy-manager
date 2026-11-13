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
    assert "Squad value" in content
    assert "Team value" in content
    assert "1 used" in content
    assert "Last move" in content
    assert f"{day:%a}, {day.day} {day:%b}" in content
    assert "Next salary update" in content
    assert "cap hit" not in content.lower()


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


def test_an_update_day_shows_the_team_value_after_it(signed_in, plan, players, season):
    """Salaries change at 22:00 right after a day's batch: that day says so."""
    from apps.fantasy.simulator import processing_moment

    season.trade_market_opens_at = processing_moment(timezone.localdate() - dt.timedelta(days=5))
    season.save()
    update_day = timezone.localdate() + dt.timedelta(days=2)
    add_trade(plan, update_day, players["G"][0], players["G"][7])
    content = signed_in.get(detail_url(plan)).content.decode()
    assert "After the 22:00 update" in content
    assert "Expected team value" in content
    assert "vs. no moves" not in content
    assert "Cash doesn't move with the update." in content


def test_a_day_without_an_update_has_no_after_update_value(signed_in, plan, players, day):
    add_trade(plan, day, players["G"][0], players["G"][7])
    content = signed_in.get(detail_url(plan)).content.decode()
    assert "Team value" in content
    assert "After the 22:00 update" not in content


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


# --- the step editor ---------------------------------------------------------


def editor_url(plan):
    return reverse("fantasy:plan-step-new", args=[plan.roster_id, plan.pk])


def test_the_editor_opens_on_the_next_processing_day(signed_in, plan, players):
    response = signed_in.get(editor_url(plan))
    assert response.status_code == 200
    context = response.context
    assert context["day"] == next_processing_day()
    assert players["G"][0] in context["squad"]
    assert all(player.pk != players["G"][0].pk for player in context["available"])


def test_the_editor_answers_for_the_chosen_day(signed_in, plan, players, day):
    """A player planned in on Saturday is in the squad from Sunday on, not before."""
    add_trade(plan, day, players["G"][0], players["G"][7])
    later = day + dt.timedelta(days=1)
    context = signed_in.get(editor_url(plan), {"day": later.isoformat()}).context
    assert players["G"][7] in context["squad"]
    assert players["G"][0] not in context["squad"]
    assert any(player.pk == players["G"][0].pk for player in context["available"])

    context = signed_in.get(editor_url(plan), {"day": day.isoformat()}).context
    # On the day itself both are taken by the move already planned.
    busy = {player.pk for player in context["squad"] if player.is_busy}
    assert players["G"][0].pk in busy
    assert all(player.pk != players["G"][7].pk for player in context["available"])


def test_a_day_not_on_offer_falls_back_to_the_first(signed_in, plan):
    context = signed_in.get(editor_url(plan), {"day": "2001-01-01"}).context
    assert context["day"] == next_processing_day()


def test_choosing_both_sides_previews_the_day(signed_in, plan, players, day):
    context = signed_in.get(
        editor_url(plan),
        {"day": day.isoformat(), "out": players["G"][0].pk, "in": players["G"][7].pk, "body": 1},
    ).context
    preview = context["preview"]
    assert preview["outcome"].cash_delta == 0
    assert preview["day"].trades_after == 1
    assert not PlanStep.objects.exists()


def test_adding_a_trade_returns_the_plan_and_closes_the_dialog(signed_in, plan, players, day):
    response = signed_in.post(
        editor_url(plan)
        + f"?kind=TRADE&day={day.isoformat()}&out={players['G'][0].pk}&in={players['G'][7].pk}",
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    assert response["HX-Trigger"] == "close-modal"
    assert 'id="plan-body"' in response.content.decode()
    (step,) = plan.steps.all()
    assert (step.player_out, step.player_in, step.processing_date) == (
        players["G"][0],
        players["G"][7],
        day,
    )


def test_a_half_made_trade_stays_in_the_dialog(signed_in, plan, players, day):
    response = signed_in.post(
        editor_url(plan) + f"?kind=TRADE&day={day.isoformat()}&out={players['G'][0].pk}",
        HTTP_HX_REQUEST="true",
    )
    assert response["HX-Retarget"] == "#plan-editor"
    assert "Pick a player to send out and one to bring in." in response.content.decode()
    assert not PlanStep.objects.exists()


def test_a_player_not_on_the_squad_that_day_cannot_be_sent_out(signed_in, plan, players, day):
    response = signed_in.post(
        editor_url(plan)
        + f"?kind=TRADE&day={day.isoformat()}&out={players['G'][7].pk}&in={players['G'][6].pk}",
        HTTP_HX_REQUEST="true",
    )
    assert response["HX-Retarget"] == "#plan-editor"
    assert not PlanStep.objects.exists()


def test_buying_a_trade_in_one_click(signed_in, plan, day):
    response = signed_in.post(
        editor_url(plan) + f"?direct=1&kind=BUY_TRADE&day={day.isoformat()}",
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    (step,) = plan.steps.all()
    assert step.kind == PlanStep.Kind.BUY_TRADE


def test_moving_a_step_to_another_day(signed_in, plan, players, day):
    step = add_trade(plan, day, players["G"][0], players["G"][7])
    url = reverse("fantasy:plan-step-move", args=[plan.roster_id, plan.pk, step.pk])
    assert signed_in.get(url).status_code == 200
    later = day + dt.timedelta(days=2)
    response = signed_in.post(url, {"day": later.isoformat()}, HTTP_HX_REQUEST="true")
    assert response["HX-Retarget"] == "#plan-body"
    step.refresh_from_db()
    assert step.processing_date == later


def test_a_step_cannot_be_moved_into_the_past(signed_in, plan, players, day):
    step = add_trade(plan, day, players["G"][0], players["G"][7])
    url = reverse("fantasy:plan-step-move", args=[plan.roster_id, plan.pk, step.pk])
    yesterday = timezone.localdate() - dt.timedelta(days=1)
    response = signed_in.post(url, {"day": yesterday.isoformat()}, HTTP_HX_REQUEST="true")
    assert "earlier batches have been processed" in response.content.decode()
    step.refresh_from_db()
    assert step.processing_date == day


def test_someone_elses_editor_is_a_404(client, plan, other_manager):
    client.force_login(other_manager.user)
    assert client.get(editor_url(plan)).status_code == 404


# --- sharing -----------------------------------------------------------------


def share_url(plan):
    return reverse("fantasy:plan-share", args=[plan.roster_id, plan.pk])


def test_sharing_makes_a_link_and_stopping_retires_it(signed_in, plan):
    response = signed_in.post(share_url(plan), {"action": "share"}, HTTP_HX_REQUEST="true")
    plan.refresh_from_db()
    first = plan.share_token
    assert first is not None
    assert reverse("fantasy:plan-shared", args=[first]) in response.content.decode()

    signed_in.post(share_url(plan), {"action": "stop"}, HTTP_HX_REQUEST="true")
    plan.refresh_from_db()
    assert plan.share_token is None

    signed_in.post(share_url(plan), {"action": "share"}, HTTP_HX_REQUEST="true")
    plan.refresh_from_db()
    assert plan.share_token not in (None, first)


def test_sharing_twice_keeps_the_same_link(plan):
    token = services.share_plan(plan).share_token
    assert services.share_plan(plan).share_token == token


def test_a_shared_plan_is_read_only_for_someone_else(client, plan, players, day, other_manager):
    add_trade(plan, day, players["G"][0], players["G"][7])
    services.share_plan(plan)
    client.force_login(other_manager.user)
    response = client.get(reverse("fantasy:plan-shared", args=[plan.share_token]))
    assert response.status_code == 200
    content = response.content.decode()
    assert "Shared by" in content and "Bulla" in content
    assert players["G"][7].full_name in content
    # No way to change it from there.
    for action in ("steps/", "rename/", "delete/", "share/"):
        assert f"/plans/{plan.pk}/{action}" not in content
    # And the owner's screens stay closed to them.
    assert client.get(detail_url(plan)).status_code == 404


def test_a_retired_link_is_a_404(client, plan, other_manager):
    token = services.share_plan(plan).share_token
    services.stop_sharing_plan(plan)
    client.force_login(other_manager.user)
    assert client.get(reverse("fantasy:plan-shared", args=[token])).status_code == 404


def test_the_shared_page_needs_a_sign_in(client, plan):
    services.share_plan(plan)
    response = client.get(reverse("fantasy:plan-shared", args=[plan.share_token]))
    assert response.status_code == 302


def test_the_owner_opening_their_own_link_is_offered_the_editor(signed_in, plan):
    services.share_plan(plan)
    response = signed_in.get(reverse("fantasy:plan-shared", args=[plan.share_token]))
    assert "Open to edit" in response.content.decode()


def test_only_the_owner_can_share(client, plan, other_manager):
    client.force_login(other_manager.user)
    assert client.post(share_url(plan), {"action": "share"}).status_code == 404


def test_how_far_away_a_day_is():
    from apps.fantasy.templatetags.plan_tags import days_away

    today = timezone.localdate()
    assert days_away(today) == "Today"
    assert days_away(today + dt.timedelta(days=1)) == "Tomorrow"
    assert days_away(today - dt.timedelta(days=1)) == "Yesterday"
    assert days_away(today + dt.timedelta(days=10)) == "in 10 days"
    assert days_away(today - dt.timedelta(days=3)) == "3 days ago"
    assert days_away(None) == ""
