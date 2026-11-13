"""The Excel download on the players list, in a real browser.
Screenshots land in tmp/screens/player-export/."""

import pytest
from django.urls import reverse
from openpyxl import load_workbook
from playwright.sync_api import expect

from apps.nba import player_export

from .conftest import page_overflow, shot

FOLDER = "player-export"
LIST = reverse("nba:player-list")
LABEL = "Download as Excel"


def visible_button(page):
    """The one download button this screen size shows."""
    buttons = page.get_by_role("link", name=LABEL)
    visible = [index for index in range(buttons.count()) if buttons.nth(index).is_visible()]
    assert len(visible) == 1, f"exactly one button shows, not {len(visible)}"
    return buttons.nth(visible[0])


def download(page, button):
    with page.expect_download() as info:
        button.click()
    file = info.value
    # Playwright saves downloads without an extension, which openpyxl refuses
    # by path -- a file handle it takes.
    with open(file.path(), "rb") as handle:
        sheet = load_workbook(handle).worksheets[0]
    headers = [cell.value for cell in sheet[player_export.HEADER_ROW]]
    rows = [
        dict(zip(headers, row, strict=True))
        for row in sheet.iter_rows(min_row=player_export.FIRST_DATA_ROW, values_only=True)
        if row[1]
    ]
    return file.suggested_filename, rows


@pytest.mark.parametrize("size", ["phone", "tablet", "desktop"])
@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_the_button_fits_every_screen(game, open_page, size, dark):
    page = open_page(game["user"], LIST, size=size, dark=dark)

    button = visible_button(page)
    if size != "phone":  # phones show the icon alone, labelled for screen readers
        expect(button).to_contain_text("Excel")
    assert page_overflow(page) == 0
    box = button.bounding_box()
    assert box["height"] >= (40 if size == "phone" else 28), "big enough to hit"
    shot(page, FOLDER, f"list-{size}-{'dark' if dark else 'light'}")


def test_a_narrow_phone_keeps_search_filters_and_download_in_one_row(game, open_page):
    page = open_page(game["user"], LIST, size="phone")
    page.set_viewport_size({"width": 320, "height": 640})

    assert page_overflow(page) == 0
    search = page.locator("#player-search").bounding_box()
    button = visible_button(page).bounding_box()
    assert search["width"] >= 120, "the search keeps room to type"
    assert abs(search["y"] - button["y"]) < 4, "same row"
    page.screenshot(path=str(page_export_dir() / "list-phone-320.png"))


def page_export_dir():
    from .conftest import SCREENS

    target = SCREENS / FOLDER
    target.mkdir(parents=True, exist_ok=True)
    return target


@pytest.mark.parametrize("size", ["phone", "desktop"])
def test_the_download_follows_a_filter_set_without_reloading(game, open_page, size):
    page = open_page(game["user"], LIST, size=size)
    if size == "phone":
        page.get_by_role("button", name="Filters").click()

    page.select_option("#player-position", "G")
    page.wait_for_url("**position=G**")
    page.wait_for_load_state("networkidle")

    name, rows = download(page, visible_button(page))

    assert name.startswith("players-") and name.endswith(".xlsx")
    assert rows, "some guards"
    assert {row["Position"] for row in rows} == {"G"}
    on_screen = page.locator("main tbody tr").count()
    assert len(rows) >= on_screen


def test_the_download_keeps_the_sort_and_skips_the_page(game, open_page):
    page = open_page(game["user"], LIST + "?sort=salary&dir=asc&page=1", size="tablet")

    _name, rows = download(page, visible_button(page))

    salaries = [row["Actual salary"] for row in rows if row["Actual salary"] is not None]
    assert salaries == sorted(salaries)
    assert len(rows) == len(game["players"]) - sum(
        1 for player in game["players"] if not player.is_active
    )


def test_the_watchlist_downloads_the_watchlist(game, open_page):
    from apps.fantasy.models import WatchlistEntry

    starred = game["players"][0]
    WatchlistEntry.objects.update_or_create(
        user=game["user"], player=starred, defaults={"stance": WatchlistEntry.Stance.WATCH}
    )
    page = open_page(game["user"], reverse("nba:watchlist"), size="phone")

    name, rows = download(page, visible_button(page))

    assert name.startswith("watchlist-")
    assert starred.full_name in {row["Player"] for row in rows}
    assert all(row["Watchlist"] == "★ Watched" for row in rows)
