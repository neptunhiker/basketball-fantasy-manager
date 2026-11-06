"""The roster timeline in a real browser, on every screen size and in both themes.

A full season's worth of moves -- pre-season back and forth, then forty trades
-- because the layout has to hold up at the busy end, not the empty one.
Screenshots land in tmp/screens/roster-timeline/.
"""

import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from .conftest import SCREENS, VIEWPORTS, page_overflow, session_cookie, shot

FOLDER = "roster-timeline"
User = get_user_model()

NAMES = [
    ("Egor", "Demin", "G"),
    ("Reed", "Sheppard", "G"),
    ("Daniss", "Jenkins", "G"),
    ("Isaiah", "Joe", "G"),
    ("Pelle", "Larsson", "G"),
    ("Darius", "Acuff Jr.", "G"),
    ("Cameron", "Boozer", "F"),
    ("Ausar", "Thompson", "F"),
    ("Sam", "Hauser", "F"),
    ("Jake", "LaRavia", "F"),
    ("Jaylen", "Wells", "F"),
    ("Baylor", "Scheierman", "F"),
    ("Derik", "Queen", "C"),
    ("Khaman", "Maluach", "C"),
    ("Day'Ron", "Sharpe", "C"),
    # Tried before the season and sold again: never on the timeline.
    ("Neemias", "Queta", "C"),
    ("Keldon", "Johnson", "F"),
    ("Ron", "Holland", "F"),
]
# Who comes in by trade, in order: a mixed bag of positions.
INCOMING = "GFGCFGGFFGCGFGFGGCFGFFGCGFGGFCGFGFGGFCGF"


@pytest.fixture
def season_of_trades(django_db_serialized_rollback, transactional_db):
    from apps.fantasy.models import (
        Manager,
        PlayerSnapshot,
        Roster,
        RosterPlayer,
        Season,
        Transaction,
    )
    from apps.nba.models import Player, Team

    call_command("seed_teams", verbosity=0)
    teams = list(Team.objects.order_by("name"))
    now = timezone.now()
    today = timezone.localdate()
    starts_on = today - dt.timedelta(days=150)
    close = timezone.make_aware(dt.datetime.combine(starts_on, dt.time(20, 30))) - dt.timedelta(
        days=1
    )
    season = Season.objects.create(
        label="2026-27",
        starts_on=starts_on,
        ends_on=today + dt.timedelta(days=22),
        is_current=True,
        signings_open_at=close - dt.timedelta(days=30),
        signings_close_at=close,
        trade_market_opens_at=close + dt.timedelta(days=26),
    )
    user = User.objects.create_user(email="coach@example.com", password="pw-for-tests")
    manager = Manager.objects.create(user=user, nick_name="Bulla")
    roster = Roster.objects.create(manager=manager, season=season, name="Koalas")

    def make(first, last, position, index):
        return Player.objects.create(
            first_name=first,
            last_name=last,
            position=position,
            team=teams[index % len(teams)],
            current_salary=Decimal(2_000_000 + (index * 377_000) % 5_000_000),
        )

    players = [make(f, last, p, i) for i, (f, last, p) in enumerate(NAMES)]
    pre_season = close - dt.timedelta(days=3)
    for player in players[:15]:
        RosterPlayer.objects.create(roster=roster, player=player, added_at=pre_season)
    for player in players[15:]:
        RosterPlayer.objects.create(
            roster=roster,
            player=player,
            added_at=pre_season - dt.timedelta(days=5),
            removed_at=pre_season,
        )

    # Forty trades: the Helpside one, a burst of four when the trade phase
    # opens, then one or two most weeks.
    days = [13, 26, 26, 26, 27, 28]
    day = 33
    while len(days) < 40:
        days.append(day)
        if len(days) % 3 == 0:
            days.append(day + 1)
        day += 4 if len(days) % 2 else 3
    days = days[:40]
    on_roster = players[:15]
    newcomers = [
        make("Rookie", f"Number{i + 1:02d}", INCOMING[i], 100 + i) for i in range(len(INCOMING))
    ]
    rotation = [3, 8, 1, 12, 5, 9, 3, 6, 10, 0, 13, 4, 8, 11, 2, 7, 3, 9, 6, 1]
    for i, offset in enumerate(days):
        when = close + dt.timedelta(days=offset, hours=1, minutes=30)
        slot = rotation[i % len(rotation)]
        out, incoming = on_roster[slot], newcomers[i]
        if i == 30:
            # One old favourite comes back.
            incoming = players[1]
        RosterPlayer.objects.filter(roster=roster, player=out, removed_at__isnull=True).update(
            removed_at=when
        )
        RosterPlayer.objects.create(roster=roster, player=incoming, added_at=when)
        Transaction.objects.create(
            roster=roster,
            occurred_at=when,
            kind=Transaction.Kind.TRADE,
            player_out=out,
            player_in=incoming,
            price_out=out.current_salary,
            price_in=incoming.current_salary,
            cash_delta=out.current_salary - incoming.current_salary,
        )
        on_roster[slot] = incoming
    # The ledger has no buys for the starters, so the balance is whatever the
    # trades left -- set, so the page shows no reconciliation warning.
    roster.cash = roster.starting_cash + sum(
        roster.transactions.values_list("cash_delta", flat=True), Decimal("0")
    )
    roster.save(update_fields=["cash"])

    # Weekly snapshots, so every stretch has a points figure.
    everyone = players + newcomers
    for week in range(0, 23):
        as_of = close + dt.timedelta(days=week * 7, hours=15)
        if as_of > now:
            break
        PlayerSnapshot.objects.bulk_create(
            PlayerSnapshot(
                player=player,
                season=season,
                as_of=as_of,
                salary=player.current_salary,
                total_fp=Decimal(week * (60 + index % 70)),
                games_played=week * 3,
                position=player.position,
            )
            for index, player in enumerate(everyone)
        )
    return {"user": user, "roster": roster}


def card_shot(page, name):
    """The timeline card on its own, which is what these checks are about."""
    target = SCREENS / FOLDER
    target.mkdir(parents=True, exist_ok=True)
    page.locator("[data-roster-timeline]").screenshot(path=str(target / f"{name}-card.png"))


def open_history(browser, live_server, data, size, dark=False, stored=None):
    viewport = {"small-desktop": {"width": 1100, "height": 900}}.get(size) or VIEWPORTS[size]
    touch = size in ("phone", "tablet")
    context = browser.new_context(
        viewport=viewport,
        color_scheme="dark" if dark else "light",
        device_scale_factor=2,
        is_mobile=size == "phone",
        has_touch=touch,
    )
    context.add_cookies([session_cookie(data["user"], live_server)])
    page = context.new_page()
    if stored:
        page.add_init_script(f"localStorage.setItem('roster-timeline-mode', '{stored}')")
    page.goto(live_server.url + reverse("fantasy:roster-history", args=[data["roster"].pk]))
    page.wait_for_load_state("networkidle")
    page.locator("[data-roster-timeline] svg").wait_for()
    return context, page


@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
@pytest.mark.parametrize(
    ("size", "mode"),
    [("phone", "scroll"), ("tablet", "scroll"), ("small-desktop", "scroll"), ("desktop", "fit")],
)
def test_the_timeline_opens_in_the_layout_that_fits(
    browser, live_server, season_of_trades, size, mode, dark
):
    context, page = open_history(browser, live_server, season_of_trades, size, dark)
    card = page.locator("[data-roster-timeline]")

    expect(card.locator(f'[data-mode="{mode}"]')).to_have_attribute("aria-pressed", "true")
    # Fifteen spots, each with today's player named on the right.
    expect(card.locator(".tl-name")).to_have_count(15)
    assert page_overflow(page) == 0
    if mode == "scroll":
        # Opens at today, with earlier weeks off to the left.
        assert card.locator("[data-chart]").evaluate("el => el.scrollLeft") > 0
    card.scroll_into_view_if_needed()
    shot(page, FOLDER, f"{size}-{'dark' if dark else 'light'}")
    card_shot(page, f"{size}-{'dark' if dark else 'light'}")
    context.close()


@pytest.mark.parametrize("size", ["phone", "tablet", "desktop"])
def test_the_other_layout_is_one_tap_away(browser, live_server, season_of_trades, size):
    context, page = open_history(browser, live_server, season_of_trades, size)
    card = page.locator("[data-roster-timeline]")
    current = card.locator('[aria-pressed="true"]').get_attribute("data-mode")
    other = "fit" if current == "scroll" else "scroll"

    card.locator(f'[data-mode="{other}"]').click()

    expect(card.locator(f'[data-mode="{other}"]')).to_have_attribute("aria-pressed", "true")
    expect(card.locator(".tl-name")).to_have_count(15)
    assert page_overflow(page) == 0
    card.scroll_into_view_if_needed()
    shot(page, FOLDER, f"{size}-{other}")
    card_shot(page, f"{size}-{other}")
    # Remembered for the next visit.
    page.reload()
    page.locator("[data-roster-timeline] svg").wait_for()
    expect(card.locator(f'[data-mode="{other}"]')).to_have_attribute("aria-pressed", "true")
    context.close()


def test_scrolling_back_keeps_todays_names_in_view(browser, live_server, season_of_trades):
    context, page = open_history(browser, live_server, season_of_trades, "phone")
    chart = page.locator("[data-roster-timeline] [data-chart]")

    chart.evaluate("el => { el.scrollLeft = 0; el.dispatchEvent(new Event('scroll')); }")
    page.wait_for_timeout(100)

    width = chart.evaluate("el => el.getBoundingClientRect().right")
    rights = page.locator(".tl-name").evaluate_all(
        "els => els.map(e => e.getBoundingClientRect().right)"
    )
    assert all(0 < right <= width for right in rights)
    shot(page, FOLDER, "phone-scrolled-back")
    card_shot(page, "phone-scrolled-back")
    context.close()


def test_a_tap_shows_the_details_on_touch_screens(browser, live_server, season_of_trades):
    context, page = open_history(browser, live_server, season_of_trades, "phone", stored="fit")
    card = page.locator("[data-roster-timeline]")

    card.locator(".tl-bar.timeline-pos-G").first.tap(force=True)

    detail = card.locator("[data-detail]")
    expect(detail).to_be_visible()
    expect(detail).to_contain_text("points")
    expect(detail.locator("a")).to_have_count(1)
    card.scroll_into_view_if_needed()
    shot(page, FOLDER, "phone-fit-detail")
    card_shot(page, "phone-fit-detail")
    context.close()


def test_hovering_shows_a_tooltip_with_a_mouse(browser, live_server, season_of_trades):
    context, page = open_history(browser, live_server, season_of_trades, "desktop")

    page.locator(".tl-bar.timeline-past-G").first.hover()

    tip = page.locator(".timeline-tip")
    expect(tip).to_be_visible()
    expect(tip).to_contain_text("Traded out")
    shot(page, FOLDER, "desktop-tooltip")
    card_shot(page, "desktop-tooltip")
    context.close()
