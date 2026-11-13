"""Trade plans in a real browser, on every screen size and in both themes.

A season under way with a salary update behind it and one coming on Sunday,
a full roster, a schedule for the next two weeks and three variants: one that
works across this week and next, one that breaks on cash, one with a move that
can no longer happen. Screenshots land in tmp/screens/trade-plans/.
"""

import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from .conftest import VIEWPORTS, page_overflow, shot

FOLDER = "trade-plans"
User = get_user_model()

SQUAD = [
    ("Donovan", "Mitchell", "G", 9.4, 44),
    ("Tyrese", "Maxey", "G", 8.1, 40),
    ("Jalen", "Brunson", "G", 7.6, 37),
    ("Coby", "White", "G", 4.2, 26),
    ("Ajay", "Mitchell", "G", 2.6, 21),
    ("Scottie", "Barnes", "F", 7.9, 38),
    ("Franz", "Wagner", "F", 6.8, 33),
    ("Jaylen", "Wells", "F", 2.1, 19),
    ("Cameron", "Boozer", "F", 3.7, 25),
    ("Ausar", "Thompson", "F", 3.3, 24),
    ("Deni", "Avdija", "F", 5.4, 30),
    ("Alperen", "Şengün", "C", 8.8, 43),
    ("Walker", "Kessler", "C", 4.9, 29),
    ("Derik", "Queen", "C", 2.4, 20),
    ("Jarrett", "Allen", "F", 5.0, 28),
]
MARKET = [
    ("Darius", "Garland", "G", 6.3, 34, 35),
    ("Cade", "Cunningham", "G", 9.9, 47, 48),
    ("Reed", "Sheppard", "G", 2.2, 18, 20),
    ("Paolo", "Banchero", "F", 6.4, 33, 32),
    ("Chet", "Holmgren", "C", 4.2, 30, 31),
]


def last_sunday_at_ten(now):
    local = timezone.localtime(now)
    days_back = (local.weekday() - 6) % 7
    sunday = (local - dt.timedelta(days=days_back)).replace(
        hour=22, minute=0, second=0, microsecond=0
    )
    if sunday > now:
        sunday -= dt.timedelta(days=7)
    return sunday


@pytest.fixture
def planned(django_db_serialized_rollback, transactional_db):
    from apps.fantasy import services
    from apps.fantasy.models import (
        Manager,
        PlanStep,
        PlayerSnapshot,
        Roster,
        RosterPlayer,
        Season,
        TradePlan,
    )
    from apps.fantasy.simulator import next_processing_day
    from apps.nba.models import Game, Player, Team

    call_command("seed_teams", verbosity=0)
    teams = list(Team.objects.order_by("name"))
    now = timezone.now()
    today = timezone.localdate()
    update = last_sunday_at_ten(now)
    season = Season.objects.create(
        label="2026-27",
        starts_on=today - dt.timedelta(days=30),
        ends_on=today + dt.timedelta(days=150),
        is_current=True,
        signings_close_at=now - dt.timedelta(days=31),
        trade_market_opens_at=update,
        trade_market_closes_at=now + dt.timedelta(days=110),
        weekly_trades_from=update + dt.timedelta(days=7),
    )
    user = User.objects.create_user(email="coach@example.com", password="pw-for-tests")
    manager = Manager.objects.create(user=user, nick_name="Bulla")
    roster = Roster.objects.create(
        manager=manager,
        season=season,
        name="Bulla Ballers",
        icon="koala",
        cash=Decimal("8350000"),
        trades_available=4,
    )

    def make(index, first, last, position, salary, fp_then, fp_now=None):
        player = Player.objects.create(
            first_name=first,
            last_name=last,
            position=position,
            team=teams[index % len(teams)],
            current_salary=Decimal(str(salary)) * 1_000_000,
            current_fp_per_game=Decimal(fp_now or fp_then),
        )
        PlayerSnapshot.objects.create(
            player=player,
            season=season,
            as_of=update + dt.timedelta(minutes=15),
            salary=player.current_salary,
            total_fp=Decimal(fp_then) * 8,
            games_played=8,
            position=position,
            team=player.team,
        )
        return player

    squad = [make(i, *row) for i, row in enumerate(SQUAD)]
    market = [make(20 + i, *row) for i, row in enumerate(MARKET)]
    for player in squad:
        RosterPlayer.objects.create(roster=roster, player=player, added_at=now - dt.timedelta(40))

    # A game every other night for every team over the next two weeks.
    number = 0
    for offset in range(15):
        night = timezone.localtime(now + dt.timedelta(days=offset)).replace(
            hour=1, minute=30, second=0, microsecond=0
        ) + dt.timedelta(days=1)
        for pair in range(offset % 2 * 2, len(teams) - 1, 4):
            number += 1
            Game.objects.create(
                espn_id=f"tp{number}",
                season_year=season.ends_on.year,
                tip_off=night,
                home_team=teams[pair],
                away_team=teams[pair + 1],
                status="STATUS_SCHEDULED",
            )

    first = next_processing_day(now)
    sunday = timezone.localtime(update + dt.timedelta(days=7)).date()
    monday = sunday + dt.timedelta(days=1)
    if first == sunday:
        first = sunday - dt.timedelta(days=1)

    good = TradePlan.objects.create(roster=roster, name="Variant 1")
    services.add_plan_step(good, sunday, player_out=squad[0], player_in=market[0])
    services.add_plan_step(good, monday, player_out=squad[3], player_in=market[2])
    services.add_plan_step(good, monday, player_out=squad[7], player_in=market[3])
    services.add_plan_step(good, monday, kind=PlanStep.Kind.BUY_TRADE)
    services.add_plan_step(
        good, monday + dt.timedelta(days=1), player_out=squad[13], player_in=market[4]
    )

    greedy = TradePlan.objects.create(roster=roster, name="All in on Cade")
    services.add_plan_step(greedy, sunday, player_out=squad[4], player_in=market[1])
    services.add_plan_step(greedy, sunday, player_out=squad[13], player_in=market[4])

    stale = services.duplicate_plan(good)
    services.rename_plan(stale, "Garland first, then wait")
    services.add_plan_step(
        stale, monday + dt.timedelta(days=2), player_out=squad[0], player_in=market[1]
    )

    return {"user": user, "roster": roster, "good": good, "greedy": greedy, "stale": stale}


def open_logged_in(browser, live_server, user, path, size, dark=False):
    from .conftest import session_cookie

    context = browser.new_context(
        viewport=VIEWPORTS[size],
        color_scheme="dark" if dark else "light",
        device_scale_factor=2,
        is_mobile=size == "phone",
        has_touch=size == "phone",
    )
    context.add_cookies([session_cookie(user, live_server)])
    page = context.new_page()
    if dark:
        page.add_init_script("localStorage.setItem('theme', 'dark')")
    page.goto(live_server.url + path)
    page.wait_for_load_state("networkidle")
    return context, page


@pytest.mark.parametrize("size", ["phone", "tablet", "desktop"])
@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_plan_pages_fit_every_screen(browser, live_server, planned, size, dark):
    roster, good = planned["roster"], planned["good"]
    theme = "dark" if dark else "light"
    pages = {
        "list": reverse("fantasy:plan-list", args=[roster.pk]),
        "detail": reverse("fantasy:plan-detail", args=[roster.pk, good.pk]),
        "broken": reverse("fantasy:plan-detail", args=[roster.pk, planned["greedy"].pk]),
        "stale": reverse("fantasy:plan-detail", args=[roster.pk, planned["stale"].pk]),
    }
    for name, path in pages.items():
        context, page = open_logged_in(browser, live_server, planned["user"], path, size, dark)
        try:
            assert page_overflow(page) == 0, f"{name} scrolls sideways at {size}"
            shot(page, FOLDER, f"{name}-{size}-{theme}")
        finally:
            context.close()


def test_the_verdicts_read_right(browser, live_server, planned):
    roster = planned["roster"]
    context, page = open_logged_in(
        browser,
        live_server,
        planned["user"],
        reverse("fantasy:plan-list", args=[roster.pk]),
        "desktop",
    )
    try:
        table = page.locator("table")
        expect(table.get_by_role("row", name="Variant 1")).to_contain_text("Works")
        expect(table.get_by_role("row", name="All in on Cade")).to_contain_text("Breaks on")
        expect(table.get_by_role("row", name="Garland first")).to_contain_text("Breaks on")
    finally:
        context.close()
