"""Analytics -> Insights in a real browser, on every screen size and in both
themes. Screenshots land in tmp/screens/insights/."""

import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from apps.analytics.dream_team import refresh
from apps.fantasy.models import Manager, PlayerSnapshot, Roster, RosterPlayer
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
    worked out after every import."""
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
    for index, player in enumerate(players):
        salary = Decimal(2_000_000 + (index * 317_000) % 11_000_000)
        pace = 18 + (index * 13) % 34
        rows.append(_snapshot(player, season, draft, salary, 0, 0))
        total = Decimal(0)
        for day, moment in enumerate(moments, start=1):
            if (index + day) % 7:  # a night off every week or so
                total += Decimal(pace + (index * day) % 9)
            rows.append(_snapshot(player, season, moment, salary, total, day))
    PlayerSnapshot.objects.bulk_create(rows)

    RosterPlayer.objects.filter(roster__season=season).delete()
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

    # A what-if that must not show.
    Roster.objects.create(manager=game["manager"], season=season, name="What If", is_active=False)
    for moment in moments:
        refresh(season, moment)

    assert cutoff(season) == close
    game["rosters"] = rosters
    return game


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
