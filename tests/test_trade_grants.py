"""The official game's trade rules: budget, free trades on a schedule, trade market.

Rosters start with the season's budget and no trades. Free trades arrive on
the season's dates -- one-off grants plus a weekly one -- and are paid out to
each roster exactly once, whenever the roster is next looked at or changed.
"""

import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from apps.fantasy import services
from apps.fantasy.models import Roster, Season, TradeGrant, Transaction
from apps.nba.models import Player

BERLIN = ZoneInfo("Europe/Berlin")


def berlin(*args):
    return dt.datetime(*args, tzinfo=BERLIN)


@pytest.fixture
def season(db):
    """2026-27 as the official game runs it."""
    season = Season.objects.create(
        label="2026-27",
        starts_on=dt.date(2026, 10, 21),
        ends_on=dt.date(2027, 4, 11),
        is_current=True,
        weekly_trades_from=berlin(2026, 11, 22, 22, 0),
        weekly_trades_per_week=1,
    )
    TradeGrant.objects.create(
        season=season, label="Helpside Trade", granted_at=berlin(2026, 11, 1, 22, 0), trades=1
    )
    TradeGrant.objects.create(
        season=season, label="Start trades", granted_at=berlin(2026, 11, 15, 22, 0), trades=3
    )
    return season


@pytest.fixture
def roster(season, manager):
    return Roster.objects.create(manager=manager, season=season, name="Roster 1")


@pytest.fixture
def running_season(db, manager):
    """A season that is live today, so trading and the market can be exercised."""
    today = timezone.localdate()
    return Season.objects.create(
        label="Live",
        starts_on=today - dt.timedelta(days=30),
        ends_on=today + dt.timedelta(days=150),
        is_current=True,
    )


@pytest.fixture
def signed_in(client, user, password):
    client.login(email=user.email, password=password)
    return client


@pytest.fixture
def admin_client(client, staff_user, password):
    staff_user.is_superuser = True
    staff_user.save(update_fields=["is_superuser"])
    client.login(email=staff_user.email, password=password)
    return client


# --- starting a roster -------------------------------------------------------------


def test_a_new_roster_starts_with_the_season_budget_and_no_trades(roster):
    assert roster.starting_cash == Decimal("63000000")
    assert roster.cash == Decimal("63000000")
    assert roster.trades_available == 0


def test_the_budget_is_set_per_season(manager, season):
    season.starting_cash = Decimal("65000000")
    season.save()

    roster = Roster.objects.create(manager=manager, season=season, name="Richer")

    assert roster.cash == Decimal("65000000")


def test_changing_the_budget_later_does_not_rewrite_an_existing_roster(roster, season):
    season.starting_cash = Decimal("70000000")
    season.save()

    roster.refresh_from_db()
    assert roster.starting_cash == Decimal("63000000")
    assert roster.cash == Decimal("63000000")


# --- the schedule ------------------------------------------------------------------


def test_weekly_trades_fall_every_sunday_at_22_berlin_time_across_the_clock_change(season):
    moments = season.weekly_trade_moments()

    local = [timezone.localtime(moment, BERLIN) for moment in moments]
    assert local[0] == berlin(2026, 11, 22, 22, 0)
    assert all(
        moment.weekday() == 6 and (moment.hour, moment.minute) == (22, 0) for moment in local
    )
    # Summer time starts on 28 March 2027; the payout stays at 22:00 local.
    assert berlin(2027, 3, 28, 22, 0) in local
    assert local[-1] == berlin(2027, 4, 11, 22, 0)
    assert len(moments) == 21


def test_the_schedule_lists_one_off_and_weekly_grants_in_order(season):
    schedule = season.trade_grant_schedule(until=berlin(2026, 11, 29, 23, 0))

    assert [(grant.label, grant.trades) for grant in schedule] == [
        ("Helpside Trade", 1),
        ("Start trades", 3),
        ("Weekly trade", 1),
        ("Weekly trade", 1),
    ]
    assert len({grant.key for grant in schedule}) == 4


def test_a_season_without_weekly_trades_has_none(running_season):
    assert running_season.weekly_trade_moments() == []
    assert running_season.trade_grant_schedule() == []


# --- paying out -----------------------------------------------------------------------


def test_nothing_is_paid_before_the_first_grant(roster):
    services.pay_due_trade_grants([roster], now=berlin(2026, 11, 1, 21, 59))

    roster.refresh_from_db()
    assert roster.trades_available == 0


def test_due_grants_are_paid_with_their_own_date(roster):
    paid = services.pay_due_trade_grants([roster], now=berlin(2026, 11, 22, 22, 0))

    roster.refresh_from_db()
    assert paid == 5  # Helpside 1 + start trades 3 + first weekly 1
    assert roster.trades_available == 5
    grants = roster.transactions.filter(kind=Transaction.Kind.GRANT_TRADE).order_by("occurred_at")
    assert [
        (timezone.localtime(g.occurred_at, BERLIN), g.trades_granted, g.note) for g in grants
    ] == [
        (berlin(2026, 11, 1, 22, 0), 1, "Helpside Trade"),
        (berlin(2026, 11, 15, 22, 0), 3, "Start trades"),
        (berlin(2026, 11, 22, 22, 0), 1, "Weekly trade"),
    ]
    assert all(g.cash_delta == 0 for g in grants)


def test_a_grant_is_never_paid_twice(roster):
    moment = berlin(2026, 11, 16, 12, 0)
    services.pay_due_trade_grants([roster], now=moment)
    services.pay_due_trade_grants([roster], now=moment)

    roster.refresh_from_db()
    assert roster.trades_available == 4


def test_used_trades_are_not_given_back(roster):
    services.pay_due_trade_grants([roster], now=berlin(2026, 11, 2, 12, 0))
    Roster.objects.filter(pk=roster.pk).update(trades_available=0)  # the Helpside Trade was used

    services.pay_due_trade_grants([roster], now=berlin(2026, 11, 16, 12, 0))

    roster.refresh_from_db()
    assert roster.trades_available == 3


def test_every_roster_in_a_list_is_brought_up_to_date(season, manager):
    rosters = [
        Roster.objects.create(manager=manager, season=season, name=f"Roster {n}") for n in range(3)
    ]

    services.pay_due_trade_grants(rosters, now=berlin(2026, 11, 2, 12, 0))

    assert [roster.trades_available for roster in rosters] == [1, 1, 1]
    assert {r.trades_available for r in Roster.objects.all()} == {1}


def test_a_trade_uses_a_grant_that_fell_due_since_the_page_was_loaded(running_season, manager):
    running_season.trade_grants.create(
        label="Start trades", granted_at=timezone.now() - dt.timedelta(hours=1), trades=3
    )
    roster = Roster.objects.create(manager=manager, season=running_season, name="R")
    out_player = Player.objects.create(last_name="Out", position="G", current_salary=1_000_000)
    in_player = Player.objects.create(last_name="In", position="G", current_salary=1_000_000)
    services.buy(roster, out_player, Decimal("1000000"))

    services.trade(roster, out_player, in_player, Decimal("1000000"), Decimal("1000000"))

    roster.refresh_from_db()
    assert roster.trades_available == 2


def test_the_build_page_pays_due_grants(signed_in, manager, running_season):
    running_season.trade_grants.create(
        label="Helpside Trade", granted_at=timezone.now() - dt.timedelta(days=1), trades=1
    )
    roster = Roster.objects.create(manager=manager, season=running_season, name="R")

    signed_in.get(reverse("fantasy:roster-build", args=[roster.pk]))

    roster.refresh_from_db()
    assert roster.trades_available == 1


def test_the_roster_list_pays_due_grants(signed_in, manager, running_season):
    running_season.trade_grants.create(
        label="Helpside Trade", granted_at=timezone.now() - dt.timedelta(days=1), trades=1
    )
    roster = Roster.objects.create(manager=manager, season=running_season, name="R")

    signed_in.get(reverse("fantasy:roster-list"))

    roster.refresh_from_db()
    assert roster.trades_available == 1


def test_the_history_shows_the_grant_and_still_reconciles(signed_in, manager, running_season):
    running_season.trade_grants.create(
        label="Helpside Trade", granted_at=timezone.now() - dt.timedelta(days=1), trades=1
    )
    roster = Roster.objects.create(manager=manager, season=running_season, name="R")

    response = signed_in.get(reverse("fantasy:roster-history", args=[roster.pk]))

    body = response.content.decode()
    assert "Helpside Trade" in body
    assert "+1 trade" in body
    assert response.context["reconciles"]


# --- the trade market ---------------------------------------------------------------


def test_trades_cannot_be_bought_before_the_market_opens(running_season, manager):
    running_season.trade_market_opens_at = timezone.now() + dt.timedelta(days=3)
    running_season.save()
    roster = Roster.objects.create(manager=manager, season=running_season, name="R")

    with pytest.raises(ValidationError, match="can be bought and sold from"):
        services.buy_trade(roster)


def test_trades_cannot_be_sold_after_the_market_closes(running_season, manager):
    running_season.trade_market_closes_at = timezone.now() - dt.timedelta(days=1)
    running_season.save()
    roster = Roster.objects.create(
        manager=manager, season=running_season, name="R", trades_available=1
    )

    with pytest.raises(ValidationError, match="closed on"):
        services.sell_trade(roster)


def test_trades_can_be_bought_and_sold_inside_the_market_window(running_season, manager):
    running_season.trade_market_opens_at = timezone.now() - dt.timedelta(days=1)
    running_season.trade_market_closes_at = timezone.now() + dt.timedelta(days=1)
    running_season.save()
    roster = Roster.objects.create(manager=manager, season=running_season, name="R")

    services.buy_trade(roster)
    services.sell_trade(roster)

    roster.refresh_from_db()
    assert roster.trades_available == 0
    assert roster.cash == Decimal("63000000") - Decimal("1500000") + Decimal("1000000")


def test_the_build_page_says_why_trades_cannot_be_bought(signed_in, manager, running_season):
    running_season.trade_market_opens_at = timezone.now() + dt.timedelta(days=3)
    running_season.save()
    roster = Roster.objects.create(manager=manager, season=running_season, name="R")

    response = signed_in.get(reverse("fantasy:roster-build", args=[roster.pk]))

    assert not response.context["can_buy_trade"]
    assert "can be bought and sold from" in response.context["buy_trade_disabled_reason"]


# --- what people see ----------------------------------------------------------------


def test_the_rules_page_shows_the_trade_calendar_team_value_and_scoring(signed_in, roster):
    body = signed_in.get(reverse("fantasy:roster-rules", args=[roster.pk])).content.decode()

    assert "Helpside Trade" in body
    assert "Start trades" in body
    assert "Weekly trade" in body
    assert "Team value" in body
    assert "Assists \N{MULTIPLICATION SIGN} 1.5" in body
    assert "$63.00M" in body


def test_the_roster_panel_shows_team_value(signed_in, manager, running_season):
    roster = Roster.objects.create(manager=manager, season=running_season, name="R")
    player = Player.objects.create(last_name="P", position="G", current_salary=5_000_000)
    services.buy(roster, player, Decimal("5000000"))
    Player.objects.filter(pk=player.pk).update(current_salary=6_000_000)  # a salary rise

    response = signed_in.get(reverse("fantasy:roster-build", args=[roster.pk]))

    # 58M cash + 6M at today's price.
    assert "$64.00M" in response.content.decode()


# --- staff: the season's grants -----------------------------------------------------


def test_only_admins_manage_trade_grants(signed_in, season):
    url = reverse("fantasy:season-trade-grants", args=[season.pk])

    assert signed_in.get(url).status_code == 403


def test_an_admin_adds_and_removes_a_grant(admin_client, season):
    url = reverse("fantasy:season-trade-grants", args=[season.pk])

    response = admin_client.post(
        url,
        {
            "action": "add",
            "label": "All-Star Game",
            "granted_at": "2027-02-14T22:00",
            "trades": "1",
        },
    )
    assert response.status_code == 200
    grant = season.trade_grants.get(label="All-Star Game")
    assert timezone.localtime(grant.granted_at, BERLIN) == berlin(2027, 2, 14, 22, 0)
    assert "All-Star Game" in response.content.decode()

    admin_client.post(url, {"action": "delete", "grant": str(grant.pk)})
    assert not season.trade_grants.filter(label="All-Star Game").exists()


def test_a_grant_needs_a_name_and_a_date(admin_client, season):
    url = reverse("fantasy:season-trade-grants", args=[season.pk])

    response = admin_client.post(
        url, {"action": "add", "label": "", "granted_at": "", "trades": "1"}
    )

    assert "This field is required." in response.content.decode()
    assert season.trade_grants.count() == 2


def test_the_season_editor_saves_the_new_rules(admin_client, season):
    response = admin_client.post(
        reverse("fantasy:season-update", args=[season.pk]),
        {
            "label": "2026-27",
            "starts_on": "2026-10-21",
            "ends_on": "2027-04-11",
            "is_current": "on",
            "signings_close_at": "2026-10-20T20:30",
            "starting_cash": "63000000",
            "weekly_trades_from": "2026-11-22T22:00",
            "weekly_trades_per_week": "1",
            "trade_market_opens_at": "2026-11-15T22:00",
            "trade_market_closes_at": "2027-03-14T22:00",
        },
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 204
    season.refresh_from_db()
    assert timezone.localtime(season.trade_market_closes_at, BERLIN) == berlin(2027, 3, 14, 22, 0)
    assert timezone.localtime(season.signings_close_at, BERLIN) == berlin(2026, 10, 20, 20, 30)


def test_the_trade_market_must_close_after_it_opens(admin_client, season):
    response = admin_client.post(
        reverse("fantasy:season-update", args=[season.pk]),
        {
            "label": "2026-27",
            "starts_on": "2026-10-21",
            "ends_on": "2027-04-11",
            "trade_market_opens_at": "2027-03-14T22:00",
            "trade_market_closes_at": "2026-11-15T22:00",
        },
    )

    assert "Trade buying must close after it opens." in response.content.decode()
