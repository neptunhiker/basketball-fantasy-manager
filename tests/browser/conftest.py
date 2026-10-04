"""Browser checks with Playwright: real pages at phone and tablet width.

Not part of the normal test run (see the `browser` marker in pyproject.toml):

    uv run playwright install chromium   # once
    uv run pytest -m browser

Screenshots land in tmp/screens/<folder>/, which git ignores.
"""

import datetime as dt
import os
from decimal import Decimal
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client
from django.utils import timezone

# Playwright's sync API runs an event loop in this thread; the test itself
# still only makes ordinary, synchronous ORM calls.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

SCREENS = Path(__file__).resolve().parents[2] / "tmp" / "screens"
PHONE = {"width": 375, "height": 812}
TABLET = {"width": 768, "height": 1024}
VIEWPORTS = {"phone": PHONE, "tablet": TABLET}

User = get_user_model()


@pytest.fixture(scope="session")
def django_db_modify_db_settings():
    """A test database of their own (suffix `_browser`).

    Live-server tests empty the database after each test, and the normal suite
    reuses its test database between runs (`--reuse-db`) -- sharing one would
    leave the next normal run without the 30 migrated teams.
    """
    from pytest_django.fixtures import _set_suffix_to_test_databases

    _set_suffix_to_test_databases(suffix="browser")


def pytest_collection_modifyitems(items):
    for item in items:
        if "tests/browser/" in str(item.fspath):
            item.add_marker(pytest.mark.browser)


@pytest.fixture
def game(django_db_serialized_rollback, transactional_db):
    # Serialized rollback: these tests run against a live server, so the
    # database is emptied after each one -- and restored from the migrated
    # state, or the reused test database would lose the 30 seeded teams.
    """A small but complete game: teams, priced players with history, a live
    season with free trades, a half-built roster, notes, staff and an admin."""
    from apps.fantasy import services
    from apps.fantasy.models import Manager, Roster, Season
    from apps.nba.models import Player, PlayerInjury, PlayerNote, Team, TeamNote

    call_command("seed_teams", verbosity=0)
    teams = list(Team.objects.order_by("name"))
    now = timezone.now()
    today = timezone.localdate()
    season = Season.objects.create(
        label="2026-27",
        starts_on=today + dt.timedelta(days=16),
        ends_on=today + dt.timedelta(days=190),
        is_current=True,
        signings_open_at=now - dt.timedelta(days=20),
        signings_close_at=now + dt.timedelta(days=15),
        weekly_trades_from=now + dt.timedelta(days=48),
        trade_market_opens_at=now + dt.timedelta(days=41),
        trade_market_closes_at=now + dt.timedelta(days=160),
    )
    season.trade_grants.create(
        label="Helpside Trade", granted_at=now + dt.timedelta(days=28), trades=1
    )
    season.trade_grants.create(
        label="Start trades", granted_at=now + dt.timedelta(days=41), trades=3
    )

    names = [
        ("Bam", "Adebayo", "C"),
        ("Giannis", "Antetokounmpo", "F"),
        ("LaMelo", "Ball", "G"),
        ("Paolo", "Banchero", "F"),
        ("Scottie", "Barnes", "F"),
        ("Devin", "Booker", "G"),
        ("Jalen", "Brunson", "G"),
        ("Jimmy", "Butler", "F"),
        ("Cade", "Cunningham", "G"),
        ("Anthony", "Davis", "C"),
        ("Luka", "Dončić", "G"),
        ("Anthony", "Edwards", "G"),
        ("Joel", "Embiid", "C"),
        ("De'Aaron", "Fox", "G"),
        ("Shai", "Gilgeous-Alexander", "G"),
        ("Rudy", "Gobert", "C"),
        ("Tyrese", "Haliburton", "G"),
        ("Chet", "Holmgren", "C"),
        ("Jaren", "Jackson Jr.", "F"),
        ("LeBron", "James", "F"),
        ("Nikola", "Jokić", "C"),
        ("Kawhi", "Leonard", "F"),
        ("Damian", "Lillard", "G"),
        ("Lauri", "Markkanen", "F"),
        ("Donovan", "Mitchell", "G"),
        ("Ja", "Morant", "G"),
        ("Jamal", "Murray", "G"),
        ("Alperen", "Şengün", "C"),
        ("Pascal", "Siakam", "F"),
        ("Jayson", "Tatum", "F"),
        ("Karl-Anthony", "Towns", "C"),
        ("Fred", "VanVleet", "G"),
        ("Victor", "Wembanyama", "C"),
        ("Zion", "Williamson", "F"),
        ("Trae", "Young", "G"),
        ("Franz", "Wagner", "F"),
        ("Evan", "Mobley", "C"),
        ("Domantas", "Sabonis", "C"),
        ("Desmond", "Bane", "G"),
        ("Mikal", "Bridges", "F"),
    ]
    players = []
    for index, (first, last, position) in enumerate(names):
        player = Player.objects.create(
            first_name=first,
            last_name=last,
            position=position,
            team=teams[index % len(teams)],
            bbde_id=1000 + index,
            is_rookie=index % 9 == 0,
        )
        salary = Decimal(2_000_000 + (index * 317_000) % 11_000_000)
        rate = Decimal(12 + (index * 7) % 30)
        # Four weekly snapshots, so Hotness and the charts have a history.
        for week in range(4):
            games = 3 * (week + 1)
            wobble = Decimal((index + week * 3) % 5 - 2)
            services.record_snapshot(
                player,
                season,
                now - dt.timedelta(days=7 * (3 - week)),
                salary=salary + Decimal(week * 150_000 * ((index % 3) - 1)),
                total_fp=(rate + wobble) * games,
                games_played=games,
                team=player.team,
                position=position,
            )
        players.append(player)

    PlayerInjury.objects.create(
        player=players[1],
        observed_at=now,
        status="Out",
        injury_type="Knee",
        short_comment="Day-to-day after a knee sprain.",
    )

    user = User.objects.create_user(
        email="coach@example.com", password="pw-for-tests", first_name="Sam", last_name="Coach"
    )
    staff = User.objects.create_user(
        email="team@example.com", password="pw-for-tests", first_name="Mia", is_staff=True
    )
    admin = User.objects.create_superuser(email="boss@example.com", password="pw-for-tests")
    manager = Manager.objects.create(user=user, nick_name="Bulla")
    Manager.objects.create(user=admin, nick_name="Boss")
    roster = Roster.objects.create(manager=manager, season=season, name="Bulla Ballers")
    for player in players[:8]:
        services.buy(roster, player, player.current_salary)
    services.add_to_watchlist(user, players[10])
    PlayerNote.objects.create(
        user=user, player=players[0], content="Great rebounder, check minutes."
    )
    TeamNote.objects.create(user=user, team=teams[0], content="Thin bench this year.")

    return {
        "season": season,
        "players": players,
        "teams": teams,
        "user": user,
        "staff": staff,
        "admin": admin,
        "manager": manager,
        "roster": roster,
    }


def session_cookie(user, live_server):
    client = Client()
    client.force_login(user)
    return {
        "name": "sessionid",
        "value": client.cookies["sessionid"].value,
        "url": live_server.url,
    }


@pytest.fixture
def open_page(browser, live_server):
    """open_page(user, path, size="phone", dark=False) -> a logged-in Playwright page."""
    contexts = []

    def _open(user, path, size="phone", dark=False):
        context = browser.new_context(
            viewport=VIEWPORTS[size],
            color_scheme="dark" if dark else "light",
            device_scale_factor=2,
            is_mobile=size == "phone",
            has_touch=size == "phone",
        )
        contexts.append(context)
        context.add_cookies([session_cookie(user, live_server)])
        page = context.new_page()
        page.goto(live_server.url + path)
        page.wait_for_load_state("networkidle")
        return page

    yield _open
    for context in contexts:
        context.close()


def shot(page, folder, name):
    """Save a full-page screenshot to tmp/screens/<folder>/<name>.png."""
    target = SCREENS / folder
    target.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(target / f"{name}.png"), full_page=True)
    # The first screen as well: what a phone shows before any scrolling.
    page.screenshot(path=str(target / f"{name}-top.png"))


def page_overflow(page):
    """How many pixels the page itself scrolls sideways (0 is right)."""
    return page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
