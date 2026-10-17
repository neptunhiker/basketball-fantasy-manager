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


@pytest.mark.parametrize("size", ["phone", "desktop"])
def test_kick_candidates_lead_the_trade_dialog(game, open_page, size):
    from apps.fantasy import services

    kicked = game["players"][5]
    services.add_to_kick_list(game["user"], kicked)
    # And one in the market, which the incoming side marks.
    services.add_to_kick_list(game["user"], game["players"][20])
    page = open_page(game["user"], build_path(game), size=size)

    # Trading opens with the season; the dialog itself renders before that.
    url = reverse("fantasy:roster-trade", args=[game["roster"].pk])
    page.evaluate(f"htmx.ajax('GET', '{url}', '#modal-root')")
    first_out = page.locator("#trade-body section").first.locator("li button").first
    first_out.wait_for()

    assert kicked.full_name in first_out.inner_text()
    assert first_out.get_by_role("img", name="On your kick list").is_visible()
    assert page_overflow(page) == 0
    close_up(page.locator("[role=dialog]").first, f"trade-{size}")

    # The incoming side, once someone is chosen to go.
    first_out.click()
    incoming = page.locator("#trade-body section").nth(1)
    incoming.get_by_role("img", name="On your kick list").wait_for()
    close_up(page.locator("[role=dialog]").first, f"trade-in-{size}")


@pytest.mark.parametrize("size", ["phone", "desktop"])
def test_the_players_list_shows_the_kick_mark_in_the_star_slot(game, open_page, size):
    from apps.fantasy import services

    player = game["players"][20]
    services.add_to_kick_list(game["user"], player)
    page = open_page(game["user"], reverse("nba:player-list"), size=size)

    button = page.get_by_role("button", name=f"Remove {player.full_name} from kick list")
    button.scroll_into_view_if_needed()
    assert button.is_visible()
    # The slot keeps the star's width, so the sticky name column stays put.
    star = page.get_by_role("button", name="Add Bam Adebayo to watchlist")
    assert abs(button.bounding_box()["width"] - star.bounding_box()["width"]) < 1
    close_up(button.locator("xpath=ancestor::tr"), f"players-row-{size}")

    button.click()
    page.get_by_role("button", name=f"Add {player.full_name} to watchlist").wait_for(timeout=3000)
    assert not WatchlistEntry.objects.filter(user=game["user"], player=player).exists()
    assert page_overflow(page) == 0
