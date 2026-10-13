"""The season stats upload pages in a real browser, at phone, tablet and desktop
width, light and dark. Screenshots land in tmp/screens/season-stats/."""

import pytest
from django.urls import reverse
from playwright.sync_api import expect

from apps.nba.models import Player
from apps.nba.season_stats import confirm_season_stats, preview_season_stats

from ..test_season_stats_parser import line, stats_file
from .conftest import page_overflow, shot

UPLOAD = reverse("nba:season-stats-upload")
SIZES = ["phone", "tablet", "desktop"]


def season_file(game):
    """A file for most of the game's players, plus every kind of line to check."""
    lines = [
        line(
            player.full_name,
            player.team.abbreviation,
            GP=60 - index,
            PTS=f"{30 - index * 0.5:.1f}",
            TFP=f"{55 - index * 0.9:.1f}",
        )
        for index, player in enumerate(game["players"][:30])
    ]
    lines += [
        line("GG Jackson", "MEM"),
        line("Kyle Lowry", "PHI"),
        line("Chris Paul", "LAC"),
        line("Broken Line", "BOS", PTS="n/a"),
    ]
    return stats_file(*lines)


@pytest.fixture
def preview(game):
    Player.objects.create(first_name="Gregory", last_name="Jackson", position="F")
    return preview_season_stats(
        season_file(game),
        season="2025-26",
        file_name="2025 2026 regular season.csv",
        triggered_by=game["admin"],
    )


def preview_url(run):
    return reverse("nba:season-stats-import", args=[run.pk])


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_the_upload_page_fits_every_screen(game, preview, open_page, size, dark):
    confirm_season_stats(preview)
    page = open_page(game["admin"], UPLOAD, size=size, dark=dark)

    assert page_overflow(page) == 0
    submit = page.get_by_role("button", name="Show preview")
    assert submit.is_disabled(), "nothing to preview before a file is chosen"
    if size == "phone":
        box = submit.bounding_box()
        assert box["height"] >= 40
        assert box["width"] > 300, "the main action spans the phone"
    shot(page, "season-stats", f"upload-{size}-{'dark' if dark else 'light'}")


def test_choosing_a_file_and_previewing_it(game, open_page, tmp_path):
    Player.objects.create(first_name="Gregory", last_name="Jackson", position="F")
    csv = tmp_path / "2025 2026 regular season.csv"
    csv.write_bytes(season_file(game))
    page = open_page(game["admin"], UPLOAD)

    page.set_input_files("input[type=file]", str(csv))
    # Alpine shows the name on the next tick; `expect` waits for it.
    expect(page.get_by_text("2025 2026 regular season.csv")).to_be_visible()
    expect(page.get_by_role("button", name="Show preview")).to_be_enabled()
    shot(page, "season-stats", "upload-file-chosen-phone")
    page.get_by_role("button", name="Show preview").click()
    page.wait_for_url("**/season-stats/import/*/")

    assert page.get_by_text("Nothing has been saved yet").is_visible()
    assert page_overflow(page) == 0


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_the_preview_fits_every_screen(game, preview, open_page, size, dark):
    page = open_page(game["admin"], preview_url(preview), size=size, dark=dark)

    assert page_overflow(page) == 0
    import_button = page.get_by_role("button", name="Import 31 players")
    assert import_button.is_visible()
    if size == "phone":
        # Pinned to the bottom edge: in reach without scrolling to the end.
        box = import_button.bounding_box()
        assert box["y"] + box["height"] <= page.viewport_size["height"]
        assert box["height"] >= 40
    shot(page, "season-stats", f"preview-{size}-{'dark' if dark else 'light'}")


def test_the_pinned_bar_never_hides_the_end_of_the_page(game, preview, open_page):
    page = open_page(game["admin"], preview_url(preview))

    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    bar = page.get_by_role("region", name="Import or discard").bounding_box()
    last_section = page.locator("main section").nth(-2).bounding_box()
    assert last_section["y"] + last_section["height"] <= bar["y"] + 1


def test_the_lines_to_check_open_and_close(game, preview, open_page):
    page = open_page(game["admin"], preview_url(preview), size="tablet")

    summary = page.get_by_text("No player found", exact=True)
    summary.click()
    assert page.get_by_text("Kyle Lowry (PHI)").is_visible()
    page.get_by_text("Matched under another name", exact=True).click()
    renamed = page.locator("details", has_text="Matched under another name")
    assert renamed.get_by_role("link", name="Gregory Jackson").is_visible()
    shot(page, "season-stats", "preview-lines-open-tablet")


def test_importing_from_the_preview(game, preview, open_page):
    page = open_page(game["admin"], preview_url(preview))

    page.get_by_role("button", name="Import 31 players").click()
    page.wait_for_load_state("networkidle")

    assert page.get_by_text("Imported 31 players for 2025-26.").is_visible()
    assert page.get_by_role("button", name="Import 31 players").count() == 0
    assert page_overflow(page) == 0
    shot(page, "season-stats", "imported-phone")


def test_the_players_list_header_still_fits_a_phone(game, open_page):
    page = open_page(game["admin"], reverse("nba:player-list"))

    assert page.get_by_role("link", name="Season stats").is_visible()
    assert page_overflow(page) == 0
    shot(page, "season-stats", "players-header-phone")


def test_the_german_preview_fits_a_phone(game, preview, open_page):
    game["admin"].language = "de"
    game["admin"].save(update_fields=["language"])
    page = open_page(game["admin"], preview_url(preview))

    assert page_overflow(page) == 0
    button = page.get_by_role("button", name="31 Spieler importieren")
    box = button.bounding_box()
    assert box["y"] + box["height"] <= page.viewport_size["height"]
    assert box["height"] < 48, "the label stays on one line"
    assert page.evaluate("el => el.scrollWidth <= el.clientWidth", button.element_handle())
    shot(page, "season-stats", "preview-phone-german")
