"""The roster's Latest tab in a real browser, on every screen size and in both
themes. Screenshots land in tmp/screens/roster-latest/."""

import datetime as dt
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from apps.fantasy import services
from apps.nba.models import PlayerNews

from .conftest import page_overflow, shot

FOLDER = "roster-latest"


@pytest.fixture
def latest(game):
    """Today's import for the fixture roster's eight players, and some news.

    Five play (one twice, one badly), one is only repriced, one only makes
    the news and one has nothing new at all.
    """
    now = timezone.now()
    # (points, games, salary change) per roster player, in roster order.
    moves = [
        (Decimal("58.5"), 1, 300_000),
        (Decimal("44"), 1, 0),
        (Decimal("-3"), 1, -100_000),
        (Decimal("61"), 2, -200_000),
        (Decimal("32.5"), 1, 100_000),
        (Decimal("0"), 0, 300_000),
        (Decimal("0"), 0, 0),
        (Decimal("0"), 0, 0),
    ]
    roster_players = game["players"][:8]
    for player, (points, games, salary) in zip(roster_players, moves, strict=True):
        newest = player.snapshots.order_by("-as_of").first()
        services.record_snapshot(
            player,
            game["season"],
            # After the fixture's newest snapshot, which is stamped `now`.
            now + dt.timedelta(minutes=5),
            salary=newest.salary + salary,
            total_fp=newest.total_fp + points,
            games_played=newest.games_played + games,
            team=player.team,
            position=player.position,
        )
    giannis, ball, brunson = roster_players[1], roster_players[2], roster_players[6]
    items = [
        (
            giannis,
            PlayerNews.Kind.INJURY,
            3,
            "Antetokounmpo (knee) questionable Friday",
            "Antetokounmpo felt soreness in his left knee after Wednesday's win. "
            "The team lists him as questionable for Friday's game.",
        ),
        (
            giannis,
            PlayerNews.Kind.NEWS,
            9,
            "Antetokounmpo posts 38-point double-double",
            "Antetokounmpo had 38 points, 14 rebounds and seven assists in 36 minutes.",
        ),
        (
            ball,
            PlayerNews.Kind.NEWS,
            5,
            "Ball struggles from deep in loss",
            "Ball went 1-for-9 from three-point range and finished with 11 points.",
        ),
        (
            brunson,
            PlayerNews.Kind.INJURY,
            2,
            "Brunson (hamstring) out at least two weeks",
            "An MRI confirmed a mild hamstring strain. Brunson will be re-evaluated in two weeks.",
        ),
    ]
    # Numbered oldest first, as RotoWire numbers its items when it publishes.
    items.sort(key=lambda item: -item[2])
    for number, (player, kind, hours, headline, body) in enumerate(items, start=1):
        PlayerNews.objects.create(
            rotowire_news_id=900 + number,
            player=player,
            kind=kind,
            headline=headline,
            body=body,
            url="https://www.rotowire.com/basketball/news.php",
            published_on=(now - dt.timedelta(hours=hours)).date(),
            first_seen_at=now - dt.timedelta(hours=hours),
            feed_player_name=player.full_name,
        )
    return game


def url(game):
    return reverse("fantasy:roster-latest", args=[game["roster"].pk])


def mode(dark):
    return "dark" if dark else "light"


@pytest.mark.parametrize("size", ["phone", "tablet", "desktop"])
@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_the_tab_fits_every_screen(latest, open_page, size, dark):
    page = open_page(latest["user"], url(latest), size=size, dark=dark)

    assert page_overflow(page) == 0
    expect(page.get_by_text("Points gained")).to_be_visible()
    shot(page, FOLDER, f"tab-{size}-{mode(dark)}")


@pytest.mark.parametrize("size", ["phone", "desktop"])
def test_a_players_news_opens_under_him(latest, open_page, size):
    page = open_page(latest["user"], url(latest), size=size)
    region = page.locator("main .md\\:hidden" if size == "phone" else "main .md\\:block")

    button = region.get_by_role("button", name="Antetokounmpo (knee) questionable Friday")
    expect(button).to_have_attribute("aria-expanded", "false")
    button.click()

    expect(button).to_have_attribute("aria-expanded", "true")
    expect(region.get_by_text("Antetokounmpo posts 38-point double-double")).to_be_visible()
    expect(region.get_by_role("link", name="Read on RotoWire").first).to_be_visible()
    assert page_overflow(page) == 0
    shot(page, FOLDER, f"news-open-{size}")


@pytest.mark.parametrize("size", ["phone", "tablet"])
def test_the_quiet_players_unfold(latest, open_page, size):
    page = open_page(latest["user"], url(latest), size=size)
    region = page.locator("main .md\\:hidden" if size == "phone" else "main .md\\:block")

    toggle = region.get_by_role("button", name="No update")
    toggle.click()

    expect(toggle).to_have_attribute("aria-expanded", "true")
    expect(region.get_by_role("link", name=latest["players"][7].full_name)).to_be_visible()


@pytest.mark.parametrize("width", [320, 390, 1024])
def test_nothing_spills_at_in_between_widths(latest, open_page, width):
    page = open_page(latest["user"], url(latest), size="phone" if width < 768 else "desktop")
    page.set_viewport_size({"width": width, "height": 900})

    assert page_overflow(page) == 0
    # Nothing in a row is clipped by the card either.
    rows = page.locator("main li, main tbody tr")
    assert rows.evaluate_all("els => els.every(el => el.scrollWidth <= el.clientWidth + 1)")
    shot(page, FOLDER, f"tab-{width}")


@pytest.mark.parametrize("width", [320, 375, 640, 768, 1440])
def test_the_roster_page_leads_to_the_tab_and_keeps_its_name(latest, open_page, width):
    build = reverse("fantasy:roster-build", args=[latest["roster"].pk])
    page = open_page(latest["user"], build, size="phone" if width < 768 else "desktop")
    page.set_viewport_size({"width": width, "height": 900})

    # In the header from `sm`, at the top of the content below it.
    where = page.locator("header" if width >= 640 else "main")
    link = where.get_by_role("link", name="Latest", exact=width >= 640)
    expect(link).to_be_visible()
    assert page.locator(f'a[href="{url(latest)}"]').locator("visible=true").count() == 1
    # The roster's name keeps the room it had before the tab existed. (At
    # 320px the header's icons already leave it only a sliver.)
    if width >= 375:
        assert page.locator("#roster-name-input").bounding_box()["width"] >= 55
    assert page_overflow(page) == 0
    shot(page, FOLDER, f"build-{width}")

    link.click()
    page.wait_for_url(f"**{url(latest)}")
