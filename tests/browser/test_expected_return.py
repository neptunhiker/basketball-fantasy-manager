"""The expected-return column and filter, in a real browser at every size."""

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.nba.models import PlayerInjury

from .conftest import VIEWPORTS, page_overflow, shot


def hurt(game):
    """Three of the squad hurt: back tomorrow, back in ten days, no date."""
    players = game["players"]
    today = timezone.localdate()
    for player, status, days in (
        (players[0], "Day-To-Day", 1),
        (players[2], "Out", 10),
        (players[3], "Out", None),
        (players[12], "Out", 4),
    ):
        PlayerInjury.objects.create(
            player=player,
            observed_at=timezone.now(),
            status=status,
            return_date=today + dt.timedelta(days=days) if days is not None else None,
        )


@pytest.mark.parametrize("size", list(VIEWPORTS))
@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_the_player_list_filters_by_return_date(game, open_page, size, dark):
    hurt(game)
    page = open_page(game["user"], reverse("nba:player-list"), size=size, dark=dark)
    if size == "phone":
        page.get_by_role("button", name="Filters").click()

    back_by = page.locator("#player-return-to")
    back_by.fill((timezone.localdate() + dt.timedelta(days=5)).isoformat())
    back_by.dispatch_event("change")
    page.wait_for_url("**return_to=*")
    page.wait_for_function("document.querySelectorAll('#player-table tbody tr').length === 2")

    width = VIEWPORTS[size]["width"]
    for field in ("#player-return-from", "#player-return-to"):
        box = page.locator(field).bounding_box()
        assert box["height"] >= (40 if size == "phone" else 30)
        # Inside the screen: the card clips, so the page itself never scrolls.
        assert box["x"] >= 0 and box["x"] + box["width"] <= width
    assert page_overflow(page) == 0
    name = f"players-{size}-{'dark' if dark else 'light'}"
    shot(page, "expected-return", name)
    # The return column sits at the table's right end; scroll there too.
    page.evaluate("document.querySelector('#player-table .overflow-x-auto').scrollLeft = 1e6")
    shot(page, "expected-return", f"{name}-scrolled")

    page.get_by_role("button", name="Clear return dates").click()
    page.wait_for_function("document.querySelectorAll('#player-table tbody tr').length > 2")


@pytest.mark.parametrize("size", list(VIEWPORTS))
def test_the_squad_sorts_by_return_date(game, open_page, size):
    hurt(game)
    path = reverse("fantasy:roster-build", args=[game["roster"].pk]) + "?sort=return"
    page = open_page(game["user"], path, size=size)

    assert page_overflow(page) == 0
    shot(page, "expected-return", f"squad-{size}")
    if size != "phone":
        page.evaluate("document.querySelector('#roster-panel .overflow-x-auto').scrollLeft = 1e6")
        shot(page, "expected-return", f"squad-{size}-scrolled")
