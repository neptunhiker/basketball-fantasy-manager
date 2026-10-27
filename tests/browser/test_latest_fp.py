"""Latest FP in the players list, on the roster and on the player page, in a
real browser. Screenshots land in tmp/screens/latest-fp/."""

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from apps.fantasy import services

from .conftest import page_overflow, shot

FOLDER = "latest-fp"
LIST = reverse("nba:player-list")


@pytest.fixture
def latest(game):
    """The next import: a game for most players, a rest day for every third."""
    # An hour after the fixture's newest snapshot, which is stamped `now`.
    now = timezone.now()
    for index, player in enumerate(game["players"]):
        newest = player.snapshots.order_by("-as_of").first()
        played = index % 3 != 0
        services.record_snapshot(
            player,
            game["season"],
            now + dt.timedelta(hours=1),
            salary=newest.salary,
            total_fp=newest.total_fp + (index % 7) * 9 - 4 if played else newest.total_fp,
            games_played=newest.games_played + (1 if played else 0),
            team=player.team,
            position=player.position,
        )
    return game


def mode(dark):
    return "dark" if dark else "light"


@pytest.mark.parametrize("size", ["phone", "tablet", "desktop"])
@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_the_list_column_fits_every_screen(latest, open_page, size, dark):
    page = open_page(latest["user"], LIST + "?sort=latest", size=size, dark=dark)

    assert page_overflow(page) == 0
    expect(page.get_by_role("link", name="Latest FP")).to_be_visible()
    index = page.locator("main thead th").evaluate_all(
        "ths => ths.findIndex(th => th.textContent.includes('Latest FP'))"
    )
    cells = page.locator(f"main tbody tr td:nth-child({index + 1})").all_inner_texts()
    rested = [text for text in cells if text.startswith("\u2013")]
    assert rested and cells[-len(rested) :] == rested, "rest days sink to the bottom"
    shot(page, FOLDER, f"list-{size}-{mode(dark)}")


def test_the_explanation_opens_on_a_phone(latest, open_page):
    page = open_page(latest["user"], LIST)

    page.get_by_role("button", name="What is Latest FP?").click()
    tip = page.get_by_role("tooltip")
    expect(tip).to_be_visible()
    box = tip.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 375


@pytest.mark.parametrize("width", [375, 768, 1024, 1280, 1440])
def test_the_player_tile_fits_every_width(latest, open_page, width):
    player = latest["players"][1]
    page = open_page(latest["user"], reverse("nba:player-detail", args=[player.slug]), "desktop")
    page.set_viewport_size({"width": width, "height": 900})

    assert page_overflow(page) == 0
    tile = page.locator("main div.grid > div", has_text="Latest FP")
    expect(tile).to_be_visible()
    # Nothing in the tile is clipped or spills into its neighbour.
    assert tile.evaluate("el => el.scrollWidth <= el.clientWidth")
    shot(page, FOLDER, f"detail-{width}")


def test_the_player_tile_says_when_he_did_not_play(latest, open_page):
    player = latest["players"][0]
    page = open_page(latest["user"], reverse("nba:player-detail", args=[player.slug]))

    tile = page.locator("main div.grid > div", has_text="Latest FP")
    expect(tile).to_contain_text("No game since")
    shot(page, FOLDER, "detail-rested-phone")


@pytest.mark.parametrize("size", ["phone", "tablet", "desktop"])
@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_the_roster_shows_it_on_every_screen(latest, open_page, size, dark):
    roster = latest["roster"]
    path = reverse("fantasy:roster-build", args=[roster.pk]) + "?sort=latest"
    page = open_page(latest["user"], path, size=size, dark=dark)

    assert page_overflow(page) == 0
    if size == "phone":
        card = page.locator("li", has_text="Latest FP").first
        expect(card).to_be_visible()
        assert card.evaluate("el => el.scrollWidth <= el.clientWidth")
    else:
        expect(page.get_by_role("link", name="Latest FP")).to_be_visible()
    shot(page, FOLDER, f"roster-{size}-{mode(dark)}")
