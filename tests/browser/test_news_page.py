"""The news page in a real browser, at phone, tablet and desktop width."""

import datetime as dt
import json
from pathlib import Path

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.nba.models import PlayerNews
from apps.nba.rotowire import sync_news

from .conftest import SCREENS, page_overflow, shot

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "rotowire" / "news_nba.json"


@pytest.fixture
def news(game):
    sync_news(json.loads(FIXTURE.read_text()))
    # Two days of news, so the page shows more than one day's heading.
    today = timezone.localdate()
    PlayerNews.objects.update(published_on=today)
    older = PlayerNews.objects.order_by("rotowire_news_id")[:8]
    PlayerNews.objects.filter(pk__in=[item.pk for item in older]).update(
        published_on=today - dt.timedelta(days=1)
    )
    return game


@pytest.mark.parametrize("size", ["phone", "tablet", "desktop"])
def test_the_news_page_fits_every_width(news, open_page, size):
    page = open_page(news["user"], reverse("nba:news"), size=size)

    assert page.locator("#news-feed article").count() == 25
    assert page.get_by_role("heading", name="Today").count() == 1
    assert page.get_by_role("heading", name="Yesterday").count() == 1
    # Booker is on the user's roster: linked, and badged with the roster.
    booker = page.locator("article", has=page.get_by_role("link", name="Devin Booker"))
    assert booker.count() == 1
    assert booker.get_by_text("Bulla Ballers").count() == 1
    assert page_overflow(page) == 0
    shot(page, "news", f"news-{size}")


@pytest.mark.parametrize("size", ["phone", "desktop"])
def test_the_news_page_in_dark_mode(news, open_page, size):
    page = open_page(news["user"], reverse("nba:news"), size=size, dark=True)

    assert page_overflow(page) == 0
    shot(page, "news", f"news-{size}-dark")


def test_the_filters_narrow_the_list(news, open_page):
    page = open_page(news["user"], reverse("nba:news"), size="phone")

    page.locator("label", has_text="Injuries").click()
    page.wait_for_function("document.querySelectorAll('#news-feed article').length === 13")
    assert "kind=injury" in page.url

    page.select_option("#news-roster", "mine")
    page.wait_for_function("document.querySelectorAll('#news-feed article').length === 0")
    assert page.get_by_text("Nothing matches those filters.").is_visible()
    shot(page, "news", "news-phone-empty")

    page.locator("label", has_text="All").first.click()
    page.wait_for_function("document.querySelectorAll('#news-feed article').length === 1")
    assert page_overflow(page) == 0
    shot(page, "news", "news-phone-my-rosters")


@pytest.mark.parametrize("size", ["phone", "desktop"])
def test_the_player_page_shows_his_news(news, open_page, size):
    sengun = next(p for p in news["players"] if p.last_name == "Şengün")
    page = open_page(news["user"], reverse("nba:player-detail", args=[sengun.slug]), size=size)

    box = page.locator("#news")
    assert box.get_by_role("heading", name="Latest news").is_visible()
    assert box.locator("article").count() == 2
    assert page_overflow(page) == 0
    shot(page, "news", f"player-{size}")
    box.screenshot(path=str(SCREENS / "news" / f"player-{size}-box.png"))
