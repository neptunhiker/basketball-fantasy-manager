"""The player list's chart loads on its own, after the table, and only when seen."""

import pytest
from django.urls import reverse
from playwright.sync_api import expect

LIST = reverse("nba:player-list")
CHART = reverse("nba:player-chart")


def dots(page):
    return page.locator(
        '#player-chart svg[aria-label="Salary compared with points per game"] circle.chart-hit'
    )


def chart_requests(page):
    requests = []
    page.on("request", lambda request: CHART in request.url and requests.append(request.url))
    return requests


@pytest.mark.parametrize("size", ["tablet", "desktop"])
def test_the_chart_arrives_and_follows_a_filter(game, open_page, size):
    page = open_page(game["user"], LIST, size=size)
    expect(dots(page).first).to_be_visible()
    everyone = dots(page).count()
    requests = chart_requests(page)

    with page.expect_response(lambda response: CHART in response.url):
        page.select_option("#player-position", "C")
    page.wait_for_load_state("networkidle")

    assert 0 < dots(page).count() < everyone
    assert len(requests) == 1
    assert "position=C" in requests[0]


def test_a_filter_redraws_only_the_season_on_show(game, open_page):
    """The other season's chart is hidden, so it is not asked for."""
    from decimal import Decimal

    from apps.nba.models import Player

    from ..test_player_season_stats import stats_for

    for player in Player.objects.all()[:10]:
        stats_for(player, team=player.team.abbreviation, game_fantasy_points=Decimal("30"))
    page = open_page(game["user"], LIST, size="desktop")
    expect(dots(page).first).to_be_visible()
    page.get_by_role("group", name="Season shown in the chart").get_by_role(
        "button", name="25/26"
    ).click()
    last_dots = page.locator(
        '#player-chart svg[aria-label^="Salary compared with fantasy"] .chart-hit'
    )
    expect(last_dots.first).to_be_visible()
    requests = chart_requests(page)

    with page.expect_response(lambda response: CHART in response.url):
        page.select_option("#player-position", "C")
    page.wait_for_load_state("networkidle")

    assert len(requests) == 1
    assert "view=last" in requests[0]


def test_a_folded_phone_chart_costs_nothing_until_opened(game, open_page):
    page = open_page(game["user"], LIST, size="phone")
    requests = chart_requests(page)

    # Filtering with the chart folded away asks for no chart.
    page.get_by_role("button", name="Filters").click()
    with page.expect_response(lambda response: "position=G" in response.url):
        page.select_option("#player-position", "G")
    page.wait_for_load_state("networkidle")
    assert requests == []

    page.get_by_role("button", name="Show chart").click()
    expect(dots(page).first).to_be_visible()
    assert len(requests) == 1
    assert "position=G" in requests[0]


def test_hovering_a_dot_names_the_player(game, open_page):
    page = open_page(game["user"], LIST, size="desktop")
    dot = dots(page).first
    expect(dot).to_be_visible()

    dot.hover()

    details = page.locator("#player-chart aside").first
    expect(details.get_by_role("link")).to_have_text(dot.get_attribute("aria-label"))


def test_widening_a_phone_past_md_shows_the_chart_without_a_click(game, open_page):
    """A rotated tablet or a wider window shows the chart by CSS alone."""
    page = open_page(game["user"], LIST, size="phone")
    assert dots(page).count() == 0

    page.set_viewport_size({"width": 1024, "height": 900})

    expect(dots(page).first).to_be_visible()


def test_a_failed_chart_request_is_tried_again(game, open_page):
    page = open_page(game["user"], LIST, size="phone")
    failed = []

    def fail_once(route):
        if not failed:
            failed.append(route.request.url)
            route.fulfill(status=500, body="boom")
        else:
            route.continue_()

    # A predicate, not a glob: the chart URL carries a query string.
    page.route(lambda url: CHART in url, fail_once)
    with page.expect_response(lambda response: CHART in response.url) as response:
        page.get_by_role("button", name="Show chart").click()
    assert response.value.status == 500
    assert failed and dots(page).count() == 0

    # Folding it and opening it again asks for the same filters once more.
    page.get_by_role("button", name="Hide chart").click()
    page.get_by_role("button", name="Show chart").click()

    expect(dots(page).first).to_be_visible()
