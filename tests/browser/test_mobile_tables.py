"""Phase 2 of the mobile plan (my_docs/MOBILE_EXP.md): tables on small screens,
checked in a real browser."""

from django.urls import reverse

from .conftest import shot


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
    salaries = sorted(
        ((p.current_salary, p.full_name) for p in game["players"][:8]), reverse=True
    )
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
    page = open_page(
        game["user"], reverse("fantasy:roster-history", args=[game["roster"].pk])
    )

    cards = page.locator("main ul.md\\:hidden > li")
    assert cards.count() >= 9  # eight signings and the season start
    shot(page, "phase2", "history-phone")
