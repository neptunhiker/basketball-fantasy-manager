"""The player list's salary chart, which is a request of its own.

It used to be drawn into every table response from the whole filtered
league. Now the table only carries a loader, and nba:player-chart draws one
season's chart for the same filters, with only the fields a dot needs.
"""

import datetime as dt
import json
import re
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.fantasy import services
from apps.nba.models import Player, Team

from .test_player_season_stats import stats_for

CHART = reverse("nba:player-chart")
LIST = reverse("nba:player-list")


@pytest.fixture
def signed_in(client, user, password):
    client.login(email=user.email, password=password)
    return client


@pytest.fixture
def teams(db):
    # Seeded by a migration, like in production.
    return {
        team.abbreviation: team for team in Team.objects.filter(abbreviation__in=["BOS", "LAL"])
    }


def make_player(last_name, team, fp_per_game="20", salary=10_000_000, **extra):
    return Player.objects.create(
        first_name="P",
        last_name=last_name,
        position=Player.Position.GUARD,
        team=team,
        current_salary=Decimal(salary),
        current_fp_per_game=Decimal(fp_per_game),
        current_total_fp=Decimal(fp_per_game) * 10,
        current_games_played=10,
        **extra,
    )


def labels(response):
    chart = response.context["chart"]
    return sorted(dot["label"] for dot in chart["dots"]) if chart else []


def test_signed_out_is_sent_to_sign_in(client, db):
    assert client.get(CHART).status_code == 302


def test_the_chart_follows_the_filters(signed_in, teams):
    make_player("Celtic", teams["BOS"])
    make_player("Laker", teams["LAL"])

    assert labels(signed_in.get(CHART)) == ["P Celtic", "P Laker"]
    assert labels(signed_in.get(CHART, {"team": "BOS"})) == ["P Celtic"]
    assert labels(signed_in.get(CHART, {"q": "lak"})) == ["P Laker"]


def test_sorting_and_paging_do_not_matter_to_it(signed_in, teams):
    make_player("Celtic", teams["BOS"])

    plain = signed_in.get(CHART)
    sorted_paged = signed_in.get(CHART, {"sort": "salary", "dir": "asc", "page": "7"})

    assert sorted_paged.status_code == 200
    assert labels(sorted_paged) == labels(plain)


def test_the_watchlist_page_has_its_own_chart(signed_in, user, teams):
    watched = make_player("Watched", teams["BOS"])
    make_player("Ignored", teams["BOS"])
    services.add_to_watchlist(user, watched)

    response = signed_in.get(reverse("nba:watchlist-chart"))

    assert labels(response) == ["P Watched"]


def test_the_list_page_points_each_season_at_its_chart(signed_in, user, teams):
    body = signed_in.get(LIST).content.decode()
    watchlist = signed_in.get(reverse("nba:watchlist")).content.decode()

    assert f"htmx.ajax('GET', '{CHART}?'" in body
    assert "query.set('view', 'current')" in body
    assert "getElementById('player-list-filters')" in body
    assert f"htmx.ajax('GET', '{reverse('nba:watchlist-chart')}?'" in watchlist


def test_the_table_no_longer_carries_a_chart(signed_in, teams):
    for index in range(5):
        make_player(f"Player{index}", teams["BOS"])

    table = signed_in.get(LIST, headers={"HX-Request": "true"}).content.decode()

    assert "chart-hit" not in table
    assert 'id="player-chart"' not in table


def test_last_season_is_asked_for_by_name(signed_in, teams):
    star = make_player("Star", teams["BOS"])
    make_player("Rookie", teams["BOS"])
    stats_for(star, team="BOS", games_played=70, game_fantasy_points=Decimal("40"))

    current = signed_in.get(CHART, {"view": "current"})
    last = signed_in.get(CHART, {"view": "last"})

    assert current.context["view"] == "current"
    assert labels(current) == ["P Rookie", "P Star"]
    assert last.context["view"] == "last"
    # Only he has a line for last season.
    assert labels(last) == ["P Star"]
    assert "Fantasy points per game 25/26" in last.content.decode()


def test_without_a_stored_season_last_falls_back_to_this_one(signed_in, teams):
    make_player("Star", teams["BOS"])

    response = signed_in.get(CHART, {"view": "last"})

    assert response.context["view"] == "current"


def test_the_chart_costs_the_same_however_many_players_it_draws(signed_in, teams):
    """A deferred field read per dot would show up here as one query per player."""
    make_player("First", teams["BOS"])
    with CaptureQueriesContext(connection) as small:
        signed_in.get(CHART)

    for index in range(30):
        make_player(f"More{index}", teams["LAL"], fp_per_game=str(10 + index))
    with CaptureQueriesContext(connection) as large:
        response = signed_in.get(CHART)

    assert len(response.context["chart"]["dots"]) == 31
    assert len(large) == len(small)


def test_the_chart_pays_only_for_the_filters_in_use(signed_in, teams):
    """Every annotation is a subquery per row; the chart skips those no filter reads."""
    make_player("Celtic", teams["BOS"])

    def chart_sql(params):
        with CaptureQueriesContext(connection) as captured:
            signed_in.get(CHART, params)
        return next(
            query["sql"]
            for query in captured.captured_queries
            if query["sql"].startswith('SELECT "nba_player"')
        )

    plain = chart_sql({})
    injured = chart_sql({"injury_status": "injured"})

    assert "nba_playerinjury" not in plain
    assert "fantasy_watchlistentry" not in plain
    assert "nba_playerinjury" in injured


def test_each_dot_is_two_circles_and_its_details_travel_as_json(signed_in, teams):
    celtic = make_player("Celtic", teams["BOS"], fp_per_game="21.5", salary=12_340_000)

    body = signed_in.get(CHART).content.decode()

    assert body.count('class="chart-hit"') == 1
    assert 'aria-label="P Celtic"' in body
    assert "data-ppg" not in body
    data = re.search(r'<script id="salary-fp-points" type="application/json">(.*?)</script>', body)
    points = json.loads(data.group(1))
    assert points[0]["label"] == "P Celtic"
    assert points[0]["ppg"] == "21.50"
    assert points[0]["salary"] == "12.34"
    assert points[0]["detailUrl"] == reverse("nba:player-detail", args=[celtic.slug])


def test_the_team_page_still_draws_its_chart_inline(signed_in, teams):
    make_player("Celtic", teams["BOS"])

    response = signed_in.get(reverse("nba:team-detail", args=["BOS"]))

    assert response.context["salary_fp_chart"]["dots"][0]["label"] == "P Celtic"
    assert 'class="chart-hit"' in response.content.decode()


def test_a_dated_filter_reaches_the_chart_too(signed_in, teams):
    """Return dates are read through the injury reports, on demand."""
    make_player("Celtic", teams["BOS"])

    response = signed_in.get(CHART, {"return_from": str(dt.date.today())})

    # Nobody is hurt, so nobody has a return date to be after.
    assert labels(response) == []
