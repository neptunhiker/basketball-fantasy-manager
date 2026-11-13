"""Played, no game or DNP, checked in a real browser at phone, tablet and desktop width."""

import datetime as dt
from decimal import Decimal

import pytest
from django.urls import reverse

from apps.fantasy import services
from apps.nba.models import Game, Player, PlayerInjury, Team

from .conftest import SCREENS, page_overflow, shot

START = dt.datetime.now(dt.UTC).replace(hour=12, minute=0, second=0, microsecond=0) - dt.timedelta(
    days=12
)


def day(n, hour=12):
    return START + dt.timedelta(days=n, hours=hour - 12)


@pytest.fixture
def flagg(game):
    """Played, rested, played twice, sat out hurt, partly played: every kind of row."""
    season = game["season"]
    dal, bos, lal, phx = (
        Team.objects.get(abbreviation=code) for code in ("DAL", "BOS", "LAL", "PHX")
    )
    who = Player.objects.create(first_name="Cooper", last_name="Flagg", position="F", team=dal)
    numbers = iter(range(1, 1000))

    def match(home, away, when):
        Game.objects.create(
            espn_id=f"b{next(numbers)}",
            season_year=season.ends_on.year,
            tip_off=when,
            home_team=home,
            away_team=away,
            status="STATUS_FINAL",
        )

    def read(n, games, points):
        services.record_snapshot(
            who,
            season,
            day(n),
            salary=Decimal("9490000"),
            total_fp=Decimal(points),
            games_played=games,
            team=dal,
            position="F",
        )

    for n in range(3):
        match(dal, bos, day(n - 6, hour=1))
    read(0, 3, 90)
    match(bos, dal, day(1, hour=1))
    read(1, 4, 131)
    read(2, 4, 131)
    match(dal, lal, day(3, hour=1))
    match(phx, dal, day(4, hour=3))
    read(4, 6, 205)
    PlayerInjury.objects.create(
        player=who, observed_at=day(4, hour=15), status="Out", injury_location="Ankle"
    )
    match(dal, phx, day(5, hour=1))
    read(5, 6, 205)
    PlayerInjury.objects.create(player=who, observed_at=day(6, hour=8), status="Healthy")
    match(lal, dal, day(6, hour=1))
    match(dal, bos, day(7, hour=1))
    read(7, 7, 236)
    who.refresh_from_db()
    return who


def any_visible(locator):
    """Whether one of the matches shows: every row renders its status twice,
    once for phones (in the pinned date cell) and once for its own column."""
    return any(locator.nth(index).is_visible() for index in range(locator.count()))


def url(player):
    return reverse("nba:player-detail", args=[player.slug])


@pytest.mark.parametrize("size", ["phone", "tablet", "desktop"])
@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_the_snapshots_show_each_kind_of_row(game, flagg, open_page, size, dark):
    page = open_page(game["user"], url(flagg), size=size, dark=dark)
    card = page.locator("#snapshots")

    assert page_overflow(page) == 0
    assert card.get_by_text("Played 7 of 9 team games").is_visible()
    # The status reads in the pinned date cell on phones, in its own column elsewhere.
    column = card.locator("thead th").nth(1)
    assert column.is_visible() == (size != "phone")
    assert any_visible(card.locator("tbody tr").first.get_by_text("1 DNP"))
    # The body part only in the roomier column; phones keep it to "Injured".
    assert any_visible(card.get_by_text("Injured (Ankle)")) == (size != "phone")
    card.screenshot(
        path=str(SCREENS / "game-status" / f"snapshots-{size}-{'dark' if dark else 'light'}.png")
    )


def test_on_a_phone_the_reason_stays_in_view_while_the_figures_scroll(game, flagg, open_page):
    page = open_page(game["user"], url(flagg))
    scroller = page.locator("#snapshots .overflow-x-auto")

    scroller.evaluate("el => { el.scrollLeft = el.scrollWidth }")

    frame = scroller.bounding_box()
    dnp_row = page.locator("#snapshots tbody tr").nth(1)
    pinned = dnp_row.locator("td").first
    assert abs(pinned.bounding_box()["x"] - frame["x"]) < 1
    chip = pinned.get_by_text("DNP", exact=True)
    assert chip.is_visible()
    assert chip.bounding_box()["x"] < frame["x"] + 140
    shot(page, "game-status", "snapshots-phone-scrolled")


def test_the_chart_reads_out_the_dnp(game, flagg, open_page):
    page = open_page(game["user"], url(flagg), size="desktop")

    dots = page.locator("#snapshots .chart-hit")
    dots.nth(4).hover()  # day 5: sat out hurt

    note = page.locator("#snapshots text.chart-readout", has_text="vs PHX")
    assert note.evaluate("el => getComputedStyle(el).opacity") == "1"
    assert "vs PHX" in note.text_content() and "Injured" in note.text_content()
    assert page.get_by_text("did not play, his team did").first.is_visible()
    page.locator("#snapshots svg").first.screenshot(
        path=str(SCREENS / "game-status" / "chart-hover.png")
    )


def test_the_team_page_marks_the_dnp_in_latest_fp(game, flagg, open_page):
    # Newest window: partly played, 1 of 2.
    page = open_page(game["user"], reverse("nba:team-detail", args=["dal"]), size="desktop")

    row = page.locator("#player-table tbody tr", has_text="Flagg")
    assert any_visible(row.get_by_text("1 DNP"))
    assert page_overflow(page) == 0


def test_the_player_page_in_german_on_a_phone(game, flagg, open_page):
    game["user"].language = "de"
    game["user"].save(update_fields=["language"])

    page = open_page(game["user"], url(flagg))

    card = page.locator("#snapshots")
    assert card.get_by_text("7 von 9 Teamspielen gespielt").is_visible()
    assert any_visible(card.get_by_text("Kein Spiel"))
    assert page_overflow(page) == 0
    card.screenshot(path=str(SCREENS / "game-status" / "snapshots-phone-de.png"))
