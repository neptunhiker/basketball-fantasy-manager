"""Phase 2 of the mobile plan (my_docs/MOBILE_EXP.md): tables on small screens,
checked in a real browser."""

from django.urls import reverse

from .conftest import page_overflow, shot


def build_url(game):
    return reverse("fantasy:roster-build", args=[game["roster"].pk])


def test_the_squad_is_cards_with_big_buttons_on_a_phone(game, open_page):
    page = open_page(game["user"], build_url(game))

    card_buttons = page.locator("#roster-panel .md\\:hidden").get_by_role("button", name="Release")
    assert card_buttons.count() == 8
    first = card_buttons.first.bounding_box()
    assert first["height"] >= 40
    assert first["x"] + first["width"] <= 375
    assert not page.locator("#roster-panel table").first.is_visible()
    page.locator("#squad-sort").scroll_into_view_if_needed()
    shot(page, "phase2", "squad-cards-phone")


def test_each_card_carries_every_figure_of_the_table_in_a_sideways_strip(game, open_page):
    page = open_page(game["user"], build_url(game))

    strip = page.locator("#roster-panel .md\\:hidden li dl").first
    labels = strip.locator("dt").all_inner_texts()
    assert [label.strip() for label in labels] == [
        "Salary",
        "Expected salary",
        "Value",
        "Points",
        "FP/G",
        "Latest FP",
        "Games",
        "Hotness",
    ]
    # The strip scrolls; the page does not.
    assert strip.evaluate("el => el.scrollWidth > el.clientWidth")
    assert page_overflow(page) == 0
    strip.scroll_into_view_if_needed()
    shot(page, "phase2", "squad-strip-phone")


def test_a_card_opens_on_the_figure_the_list_is_sorted_by(game, open_page):
    page = open_page(game["user"], build_url(game) + "?sort=hotness")

    strip = page.locator("#roster-panel .md\\:hidden li dl").first
    sorted_tile = strip.locator("[data-sorted]")
    assert "Hotness" in sorted_tile.inner_text()
    assert strip.evaluate("el => el.scrollLeft") > 0
    box, frame = sorted_tile.bounding_box(), strip.bounding_box()
    assert box["x"] >= frame["x"] and box["x"] + box["width"] <= frame["x"] + frame["width"]
    strip.scroll_into_view_if_needed()
    shot(page, "phase2", "squad-strip-sorted-phone")


def test_the_strip_help_opens_unclipped_as_a_bottom_sheet(game, open_page):
    page = open_page(game["user"], build_url(game))

    strip = page.locator("#roster-panel .md\\:hidden li dl").first
    strip.get_by_role("button", name="What is Value?").click()
    tip = strip.get_by_role("tooltip").first
    tip.wait_for(state="visible", timeout=2000)
    box = tip.bounding_box()
    assert box["width"] > 300
    shot(page, "phase2", "squad-strip-help-phone")


def test_the_squad_table_returns_on_a_tablet(game, open_page):
    page = open_page(game["user"], build_url(game), size="tablet")

    assert page.locator("#roster-panel table").first.is_visible()
    assert not page.locator("#squad-sort").is_visible()


def test_sorting_the_cards_reorders_them(game, open_page):
    page = open_page(game["user"], build_url(game))

    page.select_option("#squad-sort", "salary")
    page.wait_for_url("**sort=salary**")
    page.wait_for_load_state("networkidle")
    names = page.locator("#roster-panel .md\\:hidden li a").all_inner_texts()
    salaries = sorted(((p.current_salary, p.full_name) for p in game["players"][:8]), reverse=True)
    assert names[0] == salaries[0][1]


def test_player_names_stay_in_view_while_the_table_scrolls(game, open_page):
    page = open_page(game["user"], reverse("nba:player-list"))

    page.locator("main .overflow-x-auto").first.evaluate("el => el.scrollLeft = 400")
    first_name_cell = page.locator("main tbody tr").first.locator("td").nth(1)
    box = first_name_cell.bounding_box()
    assert 0 <= box["x"] < 80, "the name column scrolled away"
    shot(page, "phase2", "players-scrolled-phone")


def test_the_comparison_is_transposed_on_a_phone(game, open_page):
    slugs = "&".join(f"player={p.slug}" for p in game["players"][:3])
    page = open_page(game["user"], reverse("nba:player-compare") + "?" + slugs)

    rows = page.locator("main .md\\:hidden table tbody th[scope=row]")
    assert rows.count() >= 8
    assert rows.first.is_visible()
    shot(page, "phase2", "compare-phone")


def test_the_history_is_cards_on_a_phone(game, open_page):
    page = open_page(game["user"], reverse("fantasy:roster-history", args=[game["roster"].pk]))

    cards = page.locator("main ul.md\\:hidden > li")
    assert cards.count() >= 9  # eight signings and the season start
    shot(page, "phase2", "history-phone")


def test_the_snapshots_show_every_column_on_a_phone_with_the_date_pinned(game, open_page):
    player = game["players"][0]
    page = open_page(game["user"], reverse("nba:player-detail", args=[player.slug]))

    table = page.locator("table", has=page.get_by_role("columnheader", name="As of"))
    for name in ("As of", "Team", "Pos.", "Salary", "Points", "Avg/game", "Games"):
        assert table.get_by_role("columnheader", name=name).is_visible()
    frame = table.locator("xpath=..")
    assert frame.evaluate("el => el.scrollWidth > el.clientWidth")
    assert page_overflow(page) == 0

    date_cell = table.locator("tbody tr td").first
    before = date_cell.bounding_box()["x"]
    frame.evaluate("el => { el.scrollLeft = el.scrollWidth }")
    assert date_cell.bounding_box()["x"] == before
    frame.scroll_into_view_if_needed()
    shot(page, "phase2", "snapshots-phone")
