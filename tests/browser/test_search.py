"""The search boxes in a real browser, on phone, tablet and desktop.

What is checked is when a search goes out: only after a pause in typing, not
for a lone letter, and only from the box's own form. Requests are counted as
the browser sends them, so a regression in the `hx-trigger`s shows up here
even when the results would still look right.
"""

from urllib.parse import parse_qs, urlparse

import pytest
from django.urls import reverse

from .conftest import page_overflow, shot

FOLDER = "search"
SIZES = ["phone", "tablet", "desktop"]
# Brisk but human typing: well under the 500ms the boxes wait for.
FAST = 90
# Hunting for keys: a gap the old 300ms delay took for the end of a word.
HESITANT = 380
# Long enough after the last key for the delay and the response.
SETTLE = 1200


class Searches:
    """The HTMX requests a page sends to `path`, as their `q` values."""

    def __init__(self, page, path):
        self.queries = []

        def record(request):
            url = urlparse(request.url)
            if url.path == path and request.headers.get("hx-request"):
                self.queries.append(parse_qs(url.query).get("q", [""])[0])

        page.on("request", record)


def type_into(page, selector, text, delay=FAST):
    box = page.locator(selector)
    box.click()
    box.press_sequentially(text, delay=delay)
    page.wait_for_timeout(SETTLE)


# --- the players page ------------------------------------------------------------


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("delay", [FAST, HESITANT])
def test_the_players_page_searches_once_typing_stops(game, open_page, size, delay):
    path = reverse("nba:player-list")
    page = open_page(game["user"], path, size=size)
    searches = Searches(page, path)

    type_into(page, "#player-search", "james", delay=delay)

    assert searches.queries == ["james"]
    rows = page.locator("#player-table tbody tr")
    assert rows.count() >= 1
    assert all("James" in text for text in rows.all_inner_texts())
    assert page_overflow(page) == 0
    shot(page, FOLDER, f"players-{size}-{delay}")


def test_the_players_page_skips_a_lone_letter(game, open_page):
    path = reverse("nba:player-list")
    page = open_page(game["user"], path, size="desktop")
    searches = Searches(page, path)

    type_into(page, "#player-search", "j")
    assert searches.queries == []

    # A second letter searches; deleting both brings the full list back.
    type_into(page, "#player-search", "a")
    assert searches.queries == ["ja"]
    page.locator("#player-search").press("Backspace")
    page.locator("#player-search").press("Backspace")
    page.wait_for_timeout(SETTLE)
    assert searches.queries == ["ja", ""]


def test_a_slow_typist_gets_the_last_word(game, open_page):
    # A pause mid-word may search, but whatever is on screen at the end is
    # the full word's result -- an earlier response never lands last.
    path = reverse("nba:player-list")
    page = open_page(game["user"], path, size="phone")
    searches = Searches(page, path)

    type_into(page, "#player-search", "jam", delay=650)
    type_into(page, "#player-search", "es")

    assert searches.queries[-1] == "james"
    assert all("James" in text for text in page.locator("#player-table tbody tr").all_inner_texts())


# --- the roster page's market and the trade dialog ---------------------------------


@pytest.mark.parametrize("size", SIZES)
def test_the_market_searches_once_typing_stops(game, open_page, size):
    path = reverse("fantasy:roster-build", args=[game["roster"].pk])
    page = open_page(game["user"], path, size=size)
    searches = Searches(page, path)

    type_into(page, "#picker-search", "james")

    assert searches.queries == ["james"]
    assert page.locator("#picker-search").input_value() == "james"
    assert page_overflow(page) == 0
    shot(page, FOLDER, f"market-{size}")


@pytest.mark.parametrize("size", SIZES)
def test_the_trade_dialog_searches_on_its_own(game, open_page, size):
    build = reverse("fantasy:roster-build", args=[game["roster"].pk])
    trade = reverse("fantasy:roster-trade", args=[game["roster"].pk])
    page = open_page(game["user"], build, size=size)
    # Trading opens with the season; the dialog itself renders before that.
    page.evaluate(f"htmx.ajax('GET', '{trade}', '#modal-root')")
    first_out = page.locator("#trade-body section").first.locator("li button").first
    first_out.wait_for()
    # Phones show the incoming side once someone is chosen to go.
    first_out.click()
    page.locator("#trade-search").wait_for(state="visible")
    page.wait_for_timeout(300)

    market_searches = Searches(page, build)
    trade_searches = Searches(page, trade)
    type_into(page, "#trade-search", "james", delay=HESITANT)

    assert trade_searches.queries == ["james"]
    # The market behind the dialog has a search box of its own; it stays put.
    assert market_searches.queries == []
    assert page_overflow(page) == 0
    shot(page, FOLDER, f"trade-{size}")


# --- the compare dialog and the user list -------------------------------------------


@pytest.mark.parametrize("size", ["phone", "desktop"])
def test_the_compare_dialog_searches_once_typing_stops(game, open_page, size):
    modal = reverse("nba:player-compare-modal")
    page = open_page(game["user"], reverse("nba:player-compare"), size=size)
    page.evaluate(f"htmx.ajax('GET', '{modal}', '#modal-root')")
    box = "#modal-root input[type=search]"
    page.locator(box).wait_for(state="visible")
    searches = Searches(page, modal)

    type_into(page, box, "james")

    assert searches.queries == ["james"]
    assert page.locator("#compare-modal-results").get_by_text("LeBron James").is_visible()
    assert page_overflow(page) == 0


def test_the_user_list_searches_once_typing_stops(game, open_page):
    path = reverse("accounts:user-list")
    page = open_page(game["admin"], path, size="desktop")
    searches = Searches(page, path)

    type_into(page, "#user-search", "coach")

    assert searches.queries == ["coach"]
