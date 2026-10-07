"""The watchlist chip on the roster page's market, in a real browser at
phone width."""

from django.urls import reverse

from apps.fantasy import services

from .conftest import PHONE, page_overflow, shot

# The market's rows, not the position strip pinned above it.
ROWS = "#player-picker .card > ul > li"


def test_the_chip_filters_and_the_star_updates_it(game, open_page):
    # players[10] is already watched; add one more that is not on the roster.
    services.add_to_watchlist(game["user"], game["players"][11])
    page = open_page(game["user"], reverse("fantasy:roster-build", args=[game["roster"].pk]))

    chip = page.locator("#picker-filters label", has_text="Watchlist (2)")
    box = chip.bounding_box()
    assert box["height"] >= 40
    assert box["x"] + box["width"] <= PHONE["width"]

    chip.click()
    page.wait_for_function(f"document.querySelectorAll({ROWS!r}).length === 2")
    assert page.locator("#picker-filters input[name=watchlist]").is_checked()
    shot(page, "watchlist", "market-filtered-phone")

    # Unstarring a player while filtered drops the row and lowers the count.
    name = game["players"][11].full_name
    page.get_by_role("button", name=f"Remove {name} from watchlist").click()
    page.locator("#picker-filters label", has_text="Watchlist (1)").wait_for(timeout=3000)
    assert page.locator(ROWS).count() == 1
    assert page.locator("#picker-filters input[name=watchlist]").is_checked()
    assert page_overflow(page) == 0
