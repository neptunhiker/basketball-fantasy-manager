"""Analytics -> Insights in a real browser, on every screen size and in both
themes. Screenshots land in tmp/screens/insights/."""

import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db.models import F
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from apps.analytics.dream_team import refresh
from apps.fantasy.models import Manager, PlayerSnapshot, Roster, RosterPlayer, Transaction
from apps.fantasy.points import cutoff

from .conftest import page_overflow, shot

FOLDER = "insights"
URL = reverse("analytics:insights")
DAYS = 21

User = get_user_model()


@pytest.fixture
def race(game):
    """Three weeks of daily imports since signings closed, five managers with a
    roster each (one of them the game's own user), a trade, and a Dream Team
    worked out after every import.

    Priced like the real game, so the team value chart looks as it will: a
    squad of 15 fits the budget, every roster paid for its players, and two
    weekly salary updates move each price with its player's scoring pace."""
    season, players = game["season"], game["players"]
    now = timezone.now()
    close = now - dt.timedelta(days=DAYS, hours=2)
    season.signings_open_at = close - dt.timedelta(days=10)
    season.signings_close_at = close
    season.starts_on = timezone.localdate(close) + dt.timedelta(days=1)
    season.trade_market_opens_at = close + dt.timedelta(days=5)
    season.save()

    # Fresh snapshots: draft-day prices before signings close, then one import
    # a day with points that grow at each player's own pace.
    PlayerSnapshot.objects.filter(season=season).delete()
    draft = close - dt.timedelta(days=3)
    moments = [close + dt.timedelta(days=day, hours=1) for day in range(1, DAYS + 1)]
    rows = []
    salaries = {}  # player -> his salary each day
    for index, player in enumerate(players):
        drafted = Decimal(1_000_000 + (index * 317_000) % 5_000_000)
        pace = 18 + (index * 13) % 34
        # What each weekly update does to him: scorers rise, the rest fall.
        weekly = Decimal((pace - 35) * 25_000 + ((index * 7) % 5 - 2) * 60_000)
        rows.append(_snapshot(player, season, draft, drafted, 0, 0))
        total = Decimal(0)
        salaries[player.pk] = []
        for day, moment in enumerate(moments, start=1):
            if (index + day) % 7:  # a night off every week or so
                total += Decimal(pace + (index * day) % 9)
            salary = max(Decimal(500_000), drafted + weekly * ((day - 1) // 7))
            salaries[player.pk].append(salary)
            rows.append(_snapshot(player, season, moment, salary, total, day))
    PlayerSnapshot.objects.bulk_create(rows)

    RosterPlayer.objects.filter(roster__season=season).delete()
    # The game's own buys are stamped "now", after the last import: gone with
    # the squads they bought, so the ledger matches the rosters built below.
    Transaction.objects.filter(roster__season=season).delete()
    nicknames = ["Bulla", "Rival", "Hoopsie", "Splash", "Lobcity"]
    names = ["Bulla Ballers", "Kangaroo Court", "Dunk Tank", "Splash Bros FC", "Alley-Oops"]
    icons = ["koala", "tiger", "owl", "fox", "panda"]
    rosters = []
    for slot, (nick, name, icon) in enumerate(zip(nicknames, names, icons, strict=True)):
        if slot == 0:
            roster = game["roster"]
            Roster.objects.filter(pk=roster.pk).update(icon=icon)
        else:
            user = User.objects.create_user(email=f"{nick}@example.com", password="pw-for-tests")
            manager = Manager.objects.create(user=user, nick_name=nick)
            roster = Roster.objects.create(manager=manager, season=season, name=name, icon=icon)
        squad = [players[(slot * 7 + offset) % len(players)] for offset in range(15)]
        for player in squad:
            RosterPlayer.objects.create(
                roster=roster, player=player, added_at=close - dt.timedelta(days=2)
            )
        # Bought at draft-day prices, out of the season's budget.
        spent = sum(_drafted(season, player) for player in squad)
        Roster.objects.filter(pk=roster.pk).update(cash=season.starting_cash - spent)
        rosters.append(roster)

    # One trade, halfway: Dunk Tank swaps its first player for a hot one.
    traded_at = moments[10] + dt.timedelta(hours=2)
    out = RosterPlayer.objects.filter(roster=rosters[2]).order_by("added_at", "pk").first()
    out.removed_at = traded_at
    out.save()
    hot = next(
        player
        for player in players
        if not RosterPlayer.objects.filter(roster=rosters[2], player=player).exists()
    )
    RosterPlayer.objects.create(roster=rosters[2], player=hot, added_at=traded_at)
    # At that week's prices, both ways.
    price_out, price_in = (salaries[player.pk][10] for player in (out.player, hot))
    Transaction.objects.create(
        roster=rosters[2],
        occurred_at=traded_at,
        kind=Transaction.Kind.TRADE,
        player_out=out.player,
        player_in=hot,
        price_out=price_out,
        price_in=price_in,
        cash_delta=price_out - price_in,
    )
    Roster.objects.filter(pk=rosters[2].pk).update(cash=F("cash") + price_out - price_in)

    # A what-if that must not show.
    Roster.objects.create(manager=game["manager"], season=season, name="What If", is_active=False)
    for moment in moments:
        refresh(season, moment)

    assert cutoff(season) == close
    game["rosters"] = rosters
    return game


def _drafted(season, player):
    return (
        PlayerSnapshot.objects.filter(season=season, player=player, games_played=0)
        .values_list("salary", flat=True)
        .get()
    )


def _snapshot(player, season, moment, salary, total, games):
    return PlayerSnapshot(
        player=player,
        season=season,
        as_of=moment,
        salary=salary,
        total_fp=total,
        games_played=games,
        team=player.team,
        position=player.position,
    )


def mode(dark):
    return "dark" if dark else "light"


@pytest.mark.parametrize("size", ["phone", "tablet", "desktop"])
@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_the_page_fits_every_screen(race, open_page, size, dark):
    page = open_page(race["user"], URL, size=size, dark=dark)

    assert page_overflow(page) == 0
    expect(page.get_by_role("heading", name="Insights")).to_be_visible()
    expect(page.get_by_role("heading", name="Standings")).to_be_visible()
    # Five rosters and the Dream Team; the what-if is not among them.
    expect(page.locator("section[aria-labelledby=standings-heading] tbody tr")).to_have_count(6)
    expect(page.get_by_text("What If")).to_have_count(0)
    shot(page, FOLDER, f"page-{size}-{mode(dark)}")


@pytest.mark.parametrize("size", ["phone", "desktop"])
def test_the_tabs_switch_between_the_pages(race, open_page, size):
    page = open_page(race["user"], reverse("analytics:dream-team"), size=size)
    tabs = page.get_by_role("navigation", name="Analytics sections")
    expect(tabs.get_by_role("link", name="Dream Team")).to_have_attribute("aria-current", "page")
    shot(page, FOLDER, f"dream-team-tab-{size}")

    tabs.get_by_role("link", name="Insights").click()

    page.wait_for_url(f"**{URL}")
    expect(
        page.get_by_role("navigation", name="Analytics sections").get_by_role(
            "link", name="Insights"
        )
    ).to_have_attribute("aria-current", "page")


POINTS = "section[aria-labelledby=points-race-heading]"
VALUE = "section[aria-labelledby=value-race-heading]"


@pytest.mark.parametrize("size", ["phone", "tablet", "desktop"])
@pytest.mark.parametrize("key", ["points-race", "value-race"])
def test_each_screen_gets_the_drawing_made_for_it(race, open_page, size, key):
    page = open_page(race["user"], URL, size=size)
    drawings = {
        name: page.locator(f"svg:has(+ script#{key}-{name})")
        for name in ("wide", "medium", "compact")
    }
    made_for = {"phone": "compact", "tablet": "medium", "desktop": "wide"}[size]

    for name, drawing in drawings.items():
        if name == made_for:
            expect(drawing).to_be_visible()
        else:
            expect(drawing).to_be_hidden()
    shown = drawings[made_for]
    # Six lines: five rosters and the dashed Dream Team.
    expect(shown.locator("path.race-stroke[stroke-linejoin]")).to_have_count(6)
    expect(shown.locator("path[stroke-dasharray]")).to_have_count(1)
    # The value chart, and only it, marks the budget everyone started with.
    expect(shown.get_by_text("Budget")).to_have_count(1 if key == "value-race" else 0)


def test_a_mouse_reads_every_line_at_the_day_under_it(race, open_page):
    page = open_page(race["user"], URL, size="desktop")
    points, value = page.locator(POINTS), page.locator(VALUE)
    svg = points.locator("svg:has(+ script#points-race-wide)")
    box = svg.bounding_box()

    page.mouse.move(box["x"] + box["width"] * 0.45, box["y"] + box["height"] * 0.5)

    tooltip = points.locator("[data-race-tooltip]")
    expect(tooltip).to_be_visible()
    expect(tooltip.locator("li")).to_have_count(6)
    day = tooltip.locator("[data-race-tooltip-day]").inner_text()
    expect(points.locator("[data-race-day]")).to_have_text(day)
    expect(svg.locator("[data-race-hover]")).to_have_attribute("visibility", "visible")
    # Linked: the value chart shows the same day, its guide and its legend,
    # but no second tooltip.
    expect(value.locator("[data-race-day]")).to_have_text(day)
    expect(value.locator("svg:has(+ script#value-race-wide) [data-race-hover]")).to_have_attribute(
        "visibility", "visible"
    )
    expect(value.locator("[data-race-tooltip]")).to_be_hidden()
    shot(page, FOLDER, "hover-desktop")

    page.mouse.move(box["x"] + box["width"] * 0.5, box["y"] - 80)

    expect(tooltip).to_be_hidden()
    for card in (points, value):
        latest = card.locator("[data-race-day]").get_attribute("data-latest")
        expect(card.locator("[data-race-day]")).to_have_text(latest)


def test_hovering_the_value_chart_reads_out_in_millions(race, open_page):
    page = open_page(race["user"], URL, size="desktop")
    points, value = page.locator(POINTS), page.locator(VALUE)
    svg = value.locator("svg:has(+ script#value-race-wide)")
    svg.scroll_into_view_if_needed()
    box = svg.bounding_box()

    page.mouse.move(box["x"] + box["width"] * 0.7, box["y"] + box["height"] * 0.5)

    tooltip = value.locator("[data-race-tooltip]")
    expect(tooltip).to_be_visible()
    expect(tooltip.locator("li").first).to_contain_text("$")
    expect(tooltip.locator("li").first).to_contain_text("M")
    expect(points.locator("[data-race-tooltip]")).to_be_hidden()
    expect(points.locator("[data-race-day]")).to_have_text(
        tooltip.locator("[data-race-tooltip-day]").inner_text()
    )
    shot(page, FOLDER, "hover-value-desktop")


def test_a_tap_on_a_phone_reads_out_in_both_legends(race, open_page):
    page = open_page(race["user"], URL, size="phone")
    points, value = page.locator(POINTS), page.locator(VALUE)
    svg = value.locator("svg:has(+ script#value-race-compact)")
    svg.scroll_into_view_if_needed()
    latest = value.locator("[data-race-day]").inner_text()
    dream_points = points.locator("[data-race-legend='0'] [data-race-value]")
    dream_value = value.locator("[data-race-legend='0'] [data-race-value]")
    before = dream_points.inner_text(), dream_value.inner_text()

    svg.tap(position={"x": 120, "y": 100})

    expect(page.locator("[data-race-tooltip]:visible")).to_have_count(0)
    for card in (points, value):
        expect(card.locator("[data-race-day]")).not_to_have_text(latest)
    expect(dream_points).not_to_have_text(before[0])
    expect(dream_value).not_to_have_text(before[1])
    shot(page, FOLDER, "tap-phone")


def test_the_arrow_keys_step_both_charts_through_the_days(race, open_page):
    page = open_page(race["user"], URL, size="desktop")
    points, value = page.locator(POINTS), page.locator(VALUE)
    days = points.locator("[data-race-day]"), value.locator("[data-race-day]")

    points.locator("[data-race-stage]").focus()
    page.keyboard.press("Home")
    first = days[0].inner_text()
    expect(days[1]).to_have_text(first)
    page.keyboard.press("ArrowRight")

    expect(days[0]).not_to_have_text(first)
    expect(days[1]).to_have_text(days[0].inner_text())
    # Tab to the value chart: the day stays, and the keys go on from it.
    page.keyboard.press("Tab")
    expect(value.locator("[data-race-stage]")).to_be_focused()
    page.keyboard.press("ArrowLeft")
    expect(days[0]).to_have_text(first)
    expect(days[1]).to_have_text(first)
    page.keyboard.press("Escape")
    expect(page.locator("[data-race-tooltip]:visible")).to_have_count(0)


def table_fits_its_card(page):
    """The standings table no wider than its card, which clips what sticks out."""
    return page.locator("section[aria-labelledby=standings-heading]").evaluate(
        "card => card.querySelector('table').getBoundingClientRect().width <= card.clientWidth + 1"
    )


@pytest.mark.parametrize("width", [320, 390, 640, 768, 1024, 1280, 1536])
def test_nothing_spills_at_in_between_widths(race, open_page, width):
    page = open_page(race["user"], URL, size="phone" if width < 768 else "desktop")
    page.set_viewport_size({"width": width, "height": 900})

    assert page_overflow(page) == 0
    assert table_fits_its_card(page)
    # Both charts fit their cards: the drawing shown is no wider than its stage.
    for card in (POINTS, VALUE):
        assert page.locator(card).evaluate(
            "card => [...card.querySelectorAll('[data-race-svg]')]"
            ".filter(svg => svg.getBoundingClientRect().width > 0)"
            ".every(svg => svg.getBoundingClientRect().width"
            " <= card.querySelector('[data-race-stage]').clientWidth + 1)"
        )
    # From `sm` up the value has a column; below it, the roster's second line.
    column = page.locator("section[aria-labelledby=standings-heading] th", has_text="Team value")
    expect(column).to_be_visible() if width >= 640 else expect(column).to_be_hidden()
    shot(page, FOLDER, f"page-{width}")


@pytest.mark.parametrize("width", [320, 1024])
def test_german_fits_too(race, open_page, width):
    user = race["user"]
    user.language = "de"
    user.save(update_fields=["language"])
    page = open_page(user, URL, size="phone" if width < 768 else "desktop")
    page.set_viewport_size({"width": width, "height": 900})

    assert page_overflow(page) == 0
    assert table_fits_its_card(page)
    expect(page.get_by_role("heading", name="Teamwert")).to_be_visible()
    shot(page, FOLDER, f"page-de-{width}")
