"""The kick list in a real browser: the squad, the player page and the lists,
at phone, tablet and desktop width, light and dark."""

import pytest
from django.urls import reverse

from apps.fantasy.models import WatchlistEntry

from .conftest import SCREENS, VIEWPORTS, page_overflow, shot

KICK = WatchlistEntry.Stance.KICK


def close_up(locator, name):
    """A screenshot of one element, for checking the details."""
    target = SCREENS / "kick-list"
    target.mkdir(parents=True, exist_ok=True)
    locator.scroll_into_view_if_needed()
    locator.screenshot(path=str(target / f"{name}.png"))


def build_path(game):
    return reverse("fantasy:roster-build", args=[game["roster"].pk])


@pytest.mark.parametrize("size", ["phone", "tablet", "desktop"])
@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_kicking_a_squad_player_tints_his_row(game, open_page, size, dark):
    player = game["players"][2]
    page = open_page(game["user"], build_path(game), size=size, dark=dark)

    # Phones show cards, wider screens the table: one of them is visible.
    button = page.get_by_role("button", name=f"Add {player.full_name} to kick list").locator(
        "visible=true"
    )
    box = button.bounding_box()
    assert box["height"] >= (40 if size == "phone" else 32)
    button.click()
    page.get_by_role("button", name=f"Remove {player.full_name} from kick list").locator(
        "visible=true"
    ).wait_for(timeout=3000)

    assert WatchlistEntry.objects.get(user=game["user"], player=player).stance == KICK
    assert page.get_by_text("Kick candidate").locator("visible=true").count() == 1
    assert page_overflow(page) == 0
    theme = "dark" if dark else "light"
    shot(page, "kick-list", f"squad-{size}-{theme}")
    rows = page.locator("li:has([data-kicked]), tr:has([data-kicked])").locator("visible=true")
    close_up(rows.locator("xpath=..").first, f"squad-{size}-{theme}-rows")


@pytest.mark.parametrize("size", ["phone", "desktop"])
def test_star_and_kick_swap_on_the_player_page(game, open_page, size):
    # players[10] starts on the watchlist.
    player = game["players"][10]
    page = open_page(game["user"], reverse("nba:player-detail", args=[player.slug]), size=size)

    star_off = page.get_by_role("button", name=f"Remove {player.full_name} from watchlist")
    star_off.wait_for()
    page.get_by_role("button", name=f"Add {player.full_name} to kick list").click()

    page.get_by_role("button", name=f"Remove {player.full_name} from kick list").wait_for(
        timeout=3000
    )
    assert page.get_by_role("button", name=f"Add {player.full_name} to watchlist").is_visible()
    assert WatchlistEntry.objects.get(user=game["user"], player=player).stance == KICK
    assert page_overflow(page) == 0
    width = VIEWPORTS[size]["width"]
    assert page.locator("[data-player-marks]").bounding_box()["x"] < width
    shot(page, "kick-list", f"player-page-{size}")
