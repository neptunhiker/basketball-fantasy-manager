"""The trade plan simulator: a plan replayed against the roster as it stands.

Every test runs at one fixed moment -- Friday 20 November 2026, noon in Berlin
-- with the trade market open since Sunday the 15th. That places the next
salary update on Sunday the 22nd and the one after on the 29th. The Sunday
batch runs before its update, so a step up to and including Sunday is priced
at today's salaries, one on Monday at the prediction, and one a week later at
the same prediction, less trusted.
"""

import datetime as dt
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.fantasy import simulator
from apps.fantasy.models import (
    PlanStep,
    PlayerSnapshot,
    Roster,
    RosterPlayer,
    Season,
    TradeGrant,
    TradePlan,
    Transaction,
)
from apps.nba.models import Player
from apps.nba.salary_outlook import expected_amount

M = Decimal("1000000")


def berlin(*args):
    return timezone.make_aware(dt.datetime(*args))


NOW = berlin(2026, 11, 20, 12, 0)
SAT = dt.date(2026, 11, 21)
SUN = dt.date(2026, 11, 22)
MON = dt.date(2026, 11, 23)
NEXT_MON = dt.date(2026, 11, 30)
THU = dt.date(2026, 11, 19)  # already processed by NOW


@pytest.fixture
def season(db):
    return Season.objects.create(
        label="2026-27",
        starts_on=dt.date(2026, 10, 21),
        ends_on=dt.date(2027, 4, 11),
        is_current=True,
        signings_close_at=berlin(2026, 10, 20, 20, 30),
        trade_market_opens_at=berlin(2026, 11, 15, 22, 0),
        trade_market_closes_at=berlin(2027, 3, 14, 22, 0),
        weekly_trades_from=berlin(2026, 11, 22, 22, 0),
    )


def make_player(season, name, position, salary, fp_then=20, fp_now=20):
    """A player with one snapshot just after the first update.

    `fp_then` is his points per game at that update, `fp_now` today's: the
    gap between the two is what the outlook predicts his next salary from.
    """
    player = Player.objects.create(
        first_name=name,
        last_name=position,
        position=position,
        current_salary=Decimal(salary) * M,
        current_fp_per_game=Decimal(fp_now),
        # Past the six games the game needs before it reprices anyone.
        current_games_played=10,
    )
    PlayerSnapshot.objects.create(
        player=player,
        season=season,
        as_of=berlin(2026, 11, 16, 12, 15),
        salary=Decimal(salary) * M,
        total_fp=Decimal(fp_then) * 10,
        games_played=10,
        position=position,
    )
    return player


@pytest.fixture
def squad(season):
    """Five Guards, seven Forwards, three Centers: 2M apiece."""
    return {
        position: [make_player(season, f"{position}{i}", position, 2) for i in range(count)]
        for position, count in (("G", 5), ("F", 7), ("C", 3))
    }


@pytest.fixture
def market(season):
    return {
        "cheap_guard": make_player(season, "Cheap", "G", 1),
        "star_guard": make_player(season, "Star", "G", 6),
        "riser": make_player(season, "Riser", "G", 3, fp_then=20, fp_now=30),
        "forward": make_player(season, "Wing", "F", 2),
        "center": make_player(season, "Big", "C", 2),
    }


@pytest.fixture
def roster(season, manager, squad):
    roster = Roster.objects.create(
        manager=manager, season=season, name="Roster 1", cash=3 * M, trades_available=3
    )
    for players in squad.values():
        for player in players:
            RosterPlayer.objects.create(
                roster=roster, player=player, added_at=berlin(2026, 10, 15, 12, 0)
            )
    return roster


@pytest.fixture
def plan(roster):
    return TradePlan.objects.create(roster=roster, name="Variant 1")


def trade(plan, day, out, in_, order=0):
    return PlanStep.objects.create(
        plan=plan, processing_date=day, player_out=out, player_in=in_, order=order
    )


def market_step(plan, day, kind):
    return PlanStep.objects.create(plan=plan, processing_date=day, kind=kind)


def run(plan, **kwargs):
    plan.roster.refresh_from_db()
    return simulator.simulate(plan, now=NOW, **kwargs)


# --- prices ------------------------------------------------------------------


def test_an_empty_plan_works_and_changes_nothing(plan):
    result = run(plan)
    assert result.works
    assert not result.has_steps
    assert result.cash_end == result.cash_start == 3 * M
    assert result.trades_end == 3
    assert result.team_value_end == result.team_value_start == 3 * M + 15 * 2 * M


def test_before_the_next_update_a_trade_is_priced_at_todays_salaries(plan, squad, market):
    trade(plan, SAT, squad["G"][0], market["star_guard"])
    result = run(plan)
    (day,) = result.days
    assert day.price_source == simulator.KNOWN
    (step,) = day.steps
    assert (step.price_out, step.price_in) == (2 * M, 6 * M)
    assert day.cash_after == -1 * M
    assert not result.works
    assert "Cash would end the day" in day.problems[0]


def predicted_riser():
    return 3 * M + expected_amount(Decimal(30)) - expected_amount(Decimal(20))


def test_a_sunday_trade_still_uses_todays_salaries(plan, squad, market):
    """The batch runs at 22:00 before that evening's update: the old price."""
    trade(plan, SUN, squad["G"][0], market["riser"])
    result = run(plan)
    (day,) = result.days
    assert day.price_source == simulator.KNOWN
    assert day.steps[0].price_in == market["riser"].current_salary
    assert not result.has_predicted_prices


def test_a_trade_the_day_after_an_update_uses_the_new_salaries(plan, squad, market):
    trade(plan, MON, squad["G"][0], market["riser"])
    result = run(plan)
    (day,) = result.days
    assert day.price_source == simulator.PREDICTED
    assert day.steps[0].price_in == predicted_riser()
    assert predicted_riser() > market["riser"].current_salary
    assert result.has_predicted_prices


def test_two_updates_ahead_the_prediction_is_less_trusted(plan, squad, market):
    trade(plan, NEXT_MON, squad["G"][0], market["cheap_guard"])
    assert run(plan).days[0].price_source == simulator.PREDICTED_FAR


def test_a_player_without_a_prediction_keeps_todays_price(plan, squad, season):
    rookie = Player.objects.create(
        first_name="New", last_name="Guy", position="G", current_salary=1 * M
    )
    trade(plan, MON, squad["G"][0], rookie)
    assert run(plan).days[0].steps[0].price_in == 1 * M


# --- team value ----------------------------------------------------------------


def test_a_swap_moves_cash_but_not_team_value(plan, squad, market):
    trade(plan, SAT, squad["G"][0], market["cheap_guard"])
    (day,) = run(plan).days
    assert day.cash_before == 3 * M
    assert day.cash_after == 4 * M
    assert day.value_before == day.value_after == 33 * M
    assert day.value_change == 0


def test_a_bought_trade_costs_team_value(plan):
    market_step(plan, SAT, PlanStep.Kind.BUY_TRADE)
    (day,) = run(plan).days
    assert day.value_after == day.value_before - Decimal("1.5") * M


def test_only_a_day_with_an_update_says_what_it_brings(plan, squad, market):
    trade(plan, SAT, squad["G"][0], market["cheap_guard"])
    trade(plan, SUN, squad["G"][1], market["riser"])
    sat, sun = run(plan).days
    assert sat.update_at is None
    assert sat.value_after_update is None
    assert sat.update_change is None
    assert sun.update_at == berlin(2026, 11, 22, 22, 0)
    # Bought at 3M before the update, worth the prediction after it; cash stays.
    assert sun.value_after == 33 * M
    assert sun.value_after_update == 33 * M - 3 * M + predicted_riser()
    assert sun.update_change == predicted_riser() - 3 * M


def test_the_plan_is_valued_after_the_update_that_follows_it(plan, squad, market):
    trade(plan, SAT, squad["G"][0], market["riser"])  # 2M out, 3M in
    result = run(plan)
    assert result.value_end_at == berlin(2026, 11, 22, 22, 0)
    assert result.value_end_source == simulator.PREDICTED
    assert result.has_predicted_values
    assert result.team_value_start == 33 * M
    assert result.cash_end == 2 * M
    assert result.team_value_end == 33 * M - 3 * M + predicted_riser()
    # Doing nothing keeps the squad at 2M apiece: the riser is the whole gain.
    assert result.team_value_idle == 33 * M
    assert result.team_value_gain == predicted_riser() - 3 * M
    # The squad alone: the same valuation, without the cash.
    assert result.squad_value_end == 28 * M + predicted_riser()


def test_a_plan_ending_after_an_update_is_valued_at_the_next_one(plan, squad, market):
    trade(plan, MON, squad["G"][0], market["cheap_guard"])
    result = run(plan)
    assert result.value_end_at == berlin(2026, 11, 29, 22, 0)
    assert result.value_end_source == simulator.PREDICTED_FAR


def test_the_last_day_is_what_the_end_figures_are_as_of(plan, squad, market):
    assert run(plan).last_day is None
    trade(plan, MON, squad["G"][0], market["cheap_guard"])
    trade(plan, SAT, squad["G"][1], market["forward"])
    assert run(plan).last_day.date == MON


def test_an_empty_plan_gains_no_team_value(plan):
    result = run(plan)
    assert result.team_value_end == result.team_value_idle
    assert result.team_value_gain == 0
    assert not result.has_predicted_values


# --- the daily batch ---------------------------------------------------------


def test_cash_only_has_to_work_at_the_end_of_the_day(plan, squad, market):
    trade(plan, SAT, squad["G"][0], market["star_guard"], order=0)  # -4M
    trade(plan, SAT, squad["G"][1], market["cheap_guard"], order=1)  # +1M
    result = run(plan)
    assert result.days[0].cash_after == 0
    assert result.works
    assert result.trades_used == 2
    assert result.trades_end == 1


def test_positions_only_have_to_work_at_the_end_of_the_day(plan, squad, market):
    trade(plan, SAT, squad["G"][0], market["forward"], order=0)  # 4 Guards
    trade(plan, SAT, squad["F"][0], market["cheap_guard"], order=1)  # back to 5
    assert run(plan).works


def test_a_day_that_ends_short_of_a_position_fails(plan, squad, market):
    trade(plan, SAT, squad["G"][0], market["forward"])
    result = run(plan)
    assert not result.works
    assert "1 more Guard" in result.first_failure.problems[0]


def test_a_roster_already_short_is_not_blamed_on_the_plan(plan, squad, market, roster):
    RosterPlayer.objects.filter(roster=roster, player=squad["C"][0]).update(
        removed_at=berlin(2026, 11, 1, 12, 0)
    )
    RosterPlayer.objects.filter(roster=roster, player=squad["C"][1]).update(
        removed_at=berlin(2026, 11, 1, 12, 0)
    )
    roster.refresh_from_db()
    trade(plan, SAT, squad["F"][0], market["forward"])
    assert run(plan).works


# --- trades available --------------------------------------------------------


def test_running_out_of_trades_says_how_many_are_missing(plan, squad, market, roster):
    roster.trades_available = 0
    roster.save()
    trade(plan, SAT, squad["G"][0], market["cheap_guard"])
    trade(plan, SAT, squad["G"][1], market["riser"])
    day = run(plan).days[0]
    assert day.trades_short == 2
    assert not day.ok


def test_the_sunday_trade_arrives_after_that_evenings_batch(plan, squad, market, roster):
    roster.trades_available = 0
    roster.save()
    trade(plan, SUN, squad["G"][0], market["cheap_guard"])
    assert run(plan).days[0].trades_short == 1

    PlanStep.objects.all().update(processing_date=MON)
    result = run(plan)
    assert result.days[0].granted == 1
    assert result.works


def test_grants_already_due_but_not_yet_paid_count(plan, roster, season):
    TradeGrant.objects.create(
        season=season, granted_at=berlin(2026, 11, 15, 22, 0), trades=3, label="Start trades"
    )
    assert run(plan).trades_start == 6


def test_a_bought_trade_covers_a_trade_on_the_same_day(plan, squad, market, roster):
    roster.trades_available = 0
    roster.save()
    trade(plan, SAT, squad["G"][0], market["cheap_guard"])
    market_step(plan, SAT, PlanStep.Kind.BUY_TRADE)
    result = run(plan)
    assert result.works
    assert result.trades_bought == 1
    assert result.cash_end == 3 * M + 1 * M - Decimal("1500000")


def test_selling_a_trade_brings_in_a_million(plan):
    market_step(plan, SAT, PlanStep.Kind.SELL_TRADE)
    result = run(plan)
    assert result.cash_end == 4 * M
    assert result.trades_end == 2
    assert result.trades_sold == 1


def test_no_trade_market_after_it_closes(plan, season):
    market_step(plan, dt.date(2027, 3, 15), PlanStep.Kind.BUY_TRADE)
    step = run(plan).days[0].steps[0]
    assert step.status == simulator.STALE


# --- chains and stale steps --------------------------------------------------


def test_a_player_traded_in_can_be_traded_on_a_later_day(plan, squad, market):
    trade(plan, SAT, squad["G"][0], market["cheap_guard"])
    trade(plan, MON, market["cheap_guard"], squad["G"][0])
    result = run(plan)
    assert result.works
    assert squad["G"][0].pk in result.squad_end
    assert market["cheap_guard"].pk not in result.squad_end
    assert result.cash_end == 3 * M


def test_but_not_on_the_same_day(plan, squad, market):
    trade(plan, SAT, squad["G"][0], market["cheap_guard"], order=0)
    trade(plan, SAT, market["cheap_guard"], market["riser"], order=1)
    second = run(plan).days[0].steps[1]
    assert second.status == simulator.STALE
    assert not run(plan).works


def test_a_step_whose_player_has_left_is_stale_not_dropped(plan, squad, market, roster):
    trade(plan, SAT, market["forward"], market["cheap_guard"])
    result = run(plan)
    assert result.stale_steps[0].reason.endswith("will not be on the roster that day.")
    assert not result.works


def test_a_step_already_made_on_the_roster_is_done(plan, squad, market, roster):
    step = trade(plan, SAT, squad["G"][0], market["cheap_guard"])
    RosterPlayer.objects.filter(roster=roster, player=squad["G"][0]).update(removed_at=NOW)
    RosterPlayer.objects.create(roster=roster, player=market["cheap_guard"], added_at=NOW)
    Transaction.objects.create(
        roster=roster,
        occurred_at=step.created_at + dt.timedelta(minutes=1),
        kind=Transaction.Kind.TRADE,
        player_out=squad["G"][0],
        player_in=market["cheap_guard"],
        price_out=2 * M,
        price_in=1 * M,
        cash_delta=1 * M,
    )
    roster.cash = 4 * M
    roster.save()
    result = run(plan)
    assert result.days[0].steps[0].status == simulator.DONE
    assert result.works
    assert result.cash_end == 4 * M


def test_a_step_on_a_day_gone_by_was_missed(plan, squad, market):
    trade(plan, THU, squad["G"][0], market["cheap_guard"])
    result = run(plan)
    assert result.missed_steps
    # Missed is reported, not counted against the steps still ahead.
    assert result.works
    assert result.cash_end == 3 * M


def test_the_season_bounds_trades(plan, squad, market):
    trade(plan, dt.date(2027, 4, 20), squad["G"][0], market["cheap_guard"])
    assert run(plan).days[0].steps[0].reason == "The season is over by then."


# --- what the editor asks ----------------------------------------------------


def test_state_before_a_day_without_steps(plan, squad, market):
    trade(plan, SAT, squad["G"][0], market["cheap_guard"])
    result = run(plan)
    squad_ids, cash, trades = result.state_before(MON)
    assert market["cheap_guard"].pk in squad_ids
    assert cash == 4 * M
    assert trades == 2 + 1  # the weekly trade from Sunday

    squad_ids, cash, trades = result.state_before(SAT)
    assert squad["G"][0].pk in squad_ids
    assert cash == 3 * M


def test_an_unsaved_step_is_previewed_without_being_stored(plan, squad, market):
    draft = PlanStep(
        plan=plan, processing_date=SAT, player_out=squad["G"][0], player_in=market["star_guard"]
    )
    result = run(plan, extra_steps=[draft])
    assert result.days[0].steps[0].price_in == 6 * M
    assert not PlanStep.objects.exists()


def test_next_processing_day_turns_at_ten_in_the_evening():
    assert simulator.next_processing_day(berlin(2026, 11, 20, 21, 59)) == dt.date(2026, 11, 20)
    assert simulator.next_processing_day(berlin(2026, 11, 20, 22, 0)) == dt.date(2026, 11, 21)


# --- projected points --------------------------------------------------------


@pytest.fixture
def schedule(season, squad, market):
    """G0's team plays twice this week, Cheap's three times; Cheap scores more."""
    from django.core.management import call_command

    from apps.nba.models import Game, Team

    call_command("seed_teams", verbosity=0)
    mine, theirs = Team.objects.order_by("abbreviation")[:2]
    Player.objects.filter(pk=squad["G"][0].pk).update(team=mine)
    Player.objects.filter(pk=market["cheap_guard"].pk).update(
        team=theirs, current_fp_per_game=Decimal(30)
    )
    other = Team.objects.order_by("abbreviation")[5]
    for number, (team, when) in enumerate(
        [
            (mine, berlin(2026, 11, 22, 1, 30)),
            (mine, berlin(2026, 11, 24, 1, 30)),
            (theirs, berlin(2026, 11, 22, 2, 0)),
            (theirs, berlin(2026, 11, 23, 2, 0)),
            (theirs, berlin(2026, 11, 25, 2, 0)),
        ]
    ):
        Game.objects.create(
            espn_id=f"sim{number}",
            season_year=2027,
            tip_off=when,
            home_team=team,
            away_team=other,
            status="STATUS_SCHEDULED",
        )
    return mine, theirs


AVAILABILITY = Decimal("0.85")


def test_a_trade_gains_the_newcomers_points_less_the_leavers(plan, squad, market, schedule):
    trade(plan, SAT, squad["G"][0], market["cheap_guard"])
    result = run(plan)
    assert result.fp_week == (3 * 30 - 2 * 20) * AVAILABILITY
    assert result.fp_rest == 82 * (30 - 20) * AVAILABILITY
    assert result.days[0].steps[0].fp_week == result.fp_week
    assert not result.fp_incomplete


def test_a_player_kept_for_two_days_scores_only_in_between(plan, squad, market, schedule):
    trade(plan, SAT, squad["G"][0], market["cheap_guard"])
    trade(plan, MON, market["cheap_guard"], squad["G"][0])
    result = run(plan)
    # Cheap plays Sunday and Monday night, G0 would have played Saturday night.
    assert result.fp_week == (2 * 30 - 1 * 20) * AVAILABILITY
    assert result.fp_rest == result.fp_week


def test_without_a_schedule_the_points_are_unknown(plan, squad, market):
    trade(plan, SAT, squad["G"][0], market["cheap_guard"])
    result = run(plan)
    assert result.fp_rest is None
    assert result.days[0].steps[0].fp_rest is None


def test_a_plan_without_trades_gains_nothing(plan):
    market_step(plan, SAT, PlanStep.Kind.SELL_TRADE)
    assert run(plan).fp_rest == 0


def test_a_newcomer_without_games_is_unknown_not_zero(plan, squad, market, schedule):
    mine, _ = schedule
    rookie = Player.objects.create(
        first_name="New", last_name="Guy", position="G", team=mine, current_salary=1 * M
    )
    trade(plan, SAT, squad["G"][0], market["cheap_guard"], order=0)
    trade(plan, SAT, squad["G"][1], rookie, order=1)
    result = run(plan)
    known, unknown = result.days[0].steps
    assert unknown.fp_rest is None
    assert result.fp_incomplete
    assert result.fp_rest == known.fp_rest
