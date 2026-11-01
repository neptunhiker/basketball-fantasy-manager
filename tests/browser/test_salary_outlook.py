"""The next-update prediction on the player list, in a real browser."""

import datetime as dt
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.nba.models import MILLION, predict_salary

from .conftest import SCREENS, page_overflow, shot


@pytest.fixture
def repriced(game):
    """The game a few days after its second Sunday update: every player came
    out of the first with a pick premium, gained or lost a little more at the
    second, and has played on since."""
    from apps.fantasy import services

    season = game["season"]
    # The newest snapshot of the game is at about `now`; these come after it.
    now = timezone.now() + dt.timedelta(minutes=30)
    # The last Sunday 22:00 at least a day back, as the official game reprices,
    # and the one before it.
    second = timezone.localtime(now - dt.timedelta(days=1)).replace(
        hour=22, minute=0, second=0, microsecond=0
    )
    second -= dt.timedelta(days=(second.weekday() + 1) % 7)
    first = second - dt.timedelta(days=7)
    season.trade_market_opens_at = first
    season.starts_on = timezone.localdate(first) - dt.timedelta(days=5)
    season.save(update_fields=["trade_market_opens_at", "starts_on"])

    def read(player, when, salary, fp_per_game, games):
        services.record_snapshot(
            player,
            season,
            when,
            salary=max(Decimal(500_000), salary),
            total_fp=fp_per_game * games,
            games_played=games,
            team=player.team,
            position=player.position,
        )

    def expected(fp_per_game):
        return predict_salary(fp_per_game) * MILLION

    for index, player in enumerate(game["players"]):
        games = player.current_games_played
        at_first = player.current_fp_per_game
        premium = Decimal((index * 370_000) % 2_700_000 - 1_200_000)
        salary = expected(at_first) + premium
        read(player, first + dt.timedelta(minutes=15), salary, at_first, games)
        # A week of games, some well above his average and some well below.
        at_second = at_first + Decimal((index * 3) % 9 - 4) / 2
        read(player, second - dt.timedelta(hours=2), salary, at_second, games + 3)
        more_picks = Decimal((index * 130_000) % 700_000 - 300_000)
        salary = expected(at_second) + premium + more_picks
        read(player, second + dt.timedelta(minutes=15), salary, at_second, games + 3)
        at_now = at_second + Decimal((index * 5) % 23 - 11) / 4
        read(player, now, salary, at_now, games + 5)
        player.refresh_from_db()
    return game


def outlook_cells(page):
    """The Next update column's change figures, top to bottom, in millions."""
    headers = page.locator("#player-table thead th").all_inner_texts()
    column = next(i for i, text in enumerate(headers) if "NEXT UPDATE" in text.upper()) + 1
    texts = page.locator(
        f"#player-table tbody tr td:nth-child({column}) > span > span:first-child"
    ).all_inner_texts()
    assert texts, "no Next update figures found"
    values = []
    for text in texts:
        cleaned = text.replace("±", "").replace("$", "").replace("M", "").replace("+", "").strip()
        values.append(Decimal(cleaned))
    return values


@pytest.mark.parametrize("size", ["phone", "tablet", "desktop"])
def test_biggest_risers_after_an_update(repriced, open_page, size):
    page = open_page(repriced["user"], reverse("nba:player-list") + "?sort=outlook&dir=desc", size)

    assert page.get_by_text("Next salary update").is_visible()
    assert page.get_by_role("link", name="Biggest risers").get_attribute("aria-current") == "true"
    assert page_overflow(page) == 0
    headers = page.locator("#player-table thead th").all_inner_texts()
    assert any("PREMIUM" in header.upper() for header in headers)
    assert any("NEXT UPDATE" in header.upper() for header in headers)
    changes = outlook_cells(page)
    assert changes == sorted(changes, reverse=True)
    assert page_overflow(page) == 0
    shot(page, "salary-outlook", f"risers-{size}")


def test_biggest_risers_in_the_dark(repriced, open_page):
    page = open_page(
        repriced["user"], reverse("nba:player-list") + "?sort=outlook&dir=desc", "desktop", True
    )
    shot(page, "salary-outlook", "risers-desktop-dark")


def test_a_phone_sorted_by_the_prediction_shows_it_under_each_name(repriced, open_page):
    page = open_page(repriced["user"], reverse("nba:player-list") + "?sort=outlook&dir=asc")

    first_name_cell = page.locator("#player-table tbody tr").first.locator("td").nth(1)
    assert "next update" in first_name_cell.inner_text()
    page.get_by_role("button", name="What is Next update?").click()
    tip = page.get_by_role("tooltip").filter(has_text="coming Sunday update")
    tip.wait_for(state="visible", timeout=2000)
    shot(page, "salary-outlook", "fallers-phone-help")


def test_before_the_first_update_there_is_no_premium_column(game, open_page):
    page = open_page(game["user"], reverse("nba:player-list"), "desktop")

    assert page.get_by_text("No update yet this season").is_visible()
    headers = " ".join(page.locator("#player-table thead th").all_inner_texts()).upper()
    assert "NEXT UPDATE" in headers
    assert "PREMIUM" not in headers
    shot(page, "salary-outlook", "preseason-desktop")


# --- the roster -------------------------------------------------------------------


def build_url(game):
    return reverse("fantasy:roster-build", args=[game["roster"].pk])


@pytest.mark.parametrize("size", ["phone", "tablet", "desktop"])
def test_the_roster_shows_where_each_salary_is_heading(repriced, open_page, size):
    page = open_page(repriced["user"], build_url(repriced) + "?sort=outlook", size)

    assert page.get_by_text("Team value").first.is_visible()
    assert page_overflow(page) == 0
    if size == "phone":
        strip = page.locator("#roster-panel .md\\:hidden li dl").first
        labels = [label.strip() for label in strip.locator("dt").all_inner_texts()]
        assert labels[2:5] == ["Value", "Premium", "Next update"]
        assert "Next update" in strip.locator("[data-sorted]").inner_text()
        strip.scroll_into_view_if_needed()
    shot(page, "salary-outlook", f"roster-{size}")


@pytest.mark.parametrize("size", ["phone", "desktop"])
def test_the_trade_dialog_weighs_both_players_next_update(repriced, open_page, size):
    # Opened the way a user opens it: in a running season, with a trade to spend.
    season = repriced["season"]
    season.starts_on = season.starts_on.replace(year=season.starts_on.year - 1)
    season.signings_open_at = season.signings_close_at = None
    season.save()
    roster = repriced["roster"]
    roster.__class__.objects.filter(pk=roster.pk).update(trades_available=1)
    page = open_page(repriced["user"], build_url(repriced), size)

    if size == "phone":
        # The header's Trade: step 1 picks who goes out.
        page.get_by_role("button", name="Trade", exact=True).first.click()
        page.wait_for_selector("#trade-body")
        assert page.locator("#trade-body").get_by_text("-$0.60M").first.is_visible()
        page.locator("#trade-body").get_by_role("button", name="Bam Adebayo").click()
        page.get_by_text("Step 2 of 2").wait_for(state="visible", timeout=3000)
    else:
        page.get_by_role("button", name="Trade Bam Adebayo").click()
        page.wait_for_selector("#trade-body")
    page.locator("#trade-body").get_by_role("button", name="Victor Wembanyama").click()
    page.get_by_text("Team value at the next update").wait_for(state="visible", timeout=3000)

    assert page_overflow(page) == 0
    shot(page, "salary-outlook", f"trade-{size}")


# --- the player page --------------------------------------------------------------


@pytest.mark.parametrize("size", ["phone", "tablet", "desktop"])
def test_the_player_page_explains_his_next_update(repriced, open_page, size):
    player = repriced["players"][3]
    page = open_page(repriced["user"], reverse("nba:player-detail", args=[player.slug]), size)

    card = page.locator("#salary-outlook")
    assert card.get_by_text("Expected change").is_visible()
    assert card.get_by_text("Predicted salary").is_visible()
    assert card.locator("tbody tr").count() == 2
    assert page_overflow(page) == 0
    card.scroll_into_view_if_needed()
    card.screenshot(path=str(SCREENS / "salary-outlook" / f"player-{size}.png"))


def test_the_player_page_in_the_dark(repriced, open_page):
    player = repriced["players"][3]
    page = open_page(
        repriced["user"], reverse("nba:player-detail", args=[player.slug]), "phone", True
    )
    page.locator("#salary-outlook").screenshot(
        path=str(SCREENS / "salary-outlook" / "player-phone-dark.png")
    )


def test_the_german_screens_fit_a_phone(repriced, open_page):
    user = repriced["user"]
    user.language = "de"
    user.save(update_fields=["language"])
    page = open_page(user, reverse("nba:player-list") + "?sort=outlook&dir=desc")

    assert page.get_by_text("Nächstes Gehalts-Update").is_visible()
    assert page_overflow(page) == 0
    shot(page, "salary-outlook", "risers-phone-de")

    player = repriced["players"][3]
    page = open_page(user, reverse("nba:player-detail", args=[player.slug]))
    assert page_overflow(page) == 0
    page.locator("#salary-outlook").screenshot(
        path=str(SCREENS / "salary-outlook" / "player-phone-de.png")
    )
