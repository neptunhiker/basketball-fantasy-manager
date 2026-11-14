"""Performance budgets for the player pages: queries that do not grow, bytes that stay small.

Two kinds of assertion, for two kinds of regression:

* Constant cost. A page renders twice, small and large, and must cost the
  same number of queries -- the same shape as tests/test_query_counts.py.
  "Large" here also means a longer history: the player list once loaded
  every snapshot of every player, and got slower every day of the season.
* Byte budgets. The player list once sent 1.6 MB of HTML per filter change,
  most of it a chart nobody had asked for and an injury dialog per row.
  The budgets are the measured sizes plus about a quarter for headroom: a
  new column fits, a dialog per row does not. When one fails, look at what
  was added to every row before raising the number.

The data is shaped like a real page: a full page of 50 players, each with a
history of snapshots, every tenth one hurt.
"""

import datetime as dt
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.fantasy import services
from apps.fantasy.models import Season
from apps.nba.models import Player, PlayerInjury, Team

LIST = reverse("nba:player-list")
CHART = reverse("nba:player-chart")
KB = 1024


@pytest.fixture
def season(db):
    return Season.objects.create(
        label="2026-27",
        starts_on=dt.date(2026, 10, 20),
        ends_on=dt.date(2027, 4, 11),
        is_current=True,
    )


@pytest.fixture
def signed_in(client, user, password):
    client.login(email=user.email, password=password)
    return client


def add_players(season, count, days=3, start=0):
    """`count` priced players on one team, each with `days` daily snapshots."""
    team = Team.objects.get(abbreviation="BOS")
    first_day = timezone.now() - dt.timedelta(days=days + 1)
    players = []
    for index in range(start, start + count):
        player = Player.objects.create(
            first_name="Player", last_name=f"Number{index:03d}", position="GF"[index % 2], team=team
        )
        for day in range(days):
            services.record_snapshot(
                player,
                season,
                first_day + dt.timedelta(days=day),
                salary=Decimal(5_000_000 + index * 10_000),
                total_fp=Decimal(20 + index % 7) * (day + 1),
                games_played=day + 1,
                team=team,
                position=player.position,
            )
        if index % 10 == 0:
            PlayerInjury.objects.create(
                player=player, observed_at=timezone.now(), status="Out", injury_type="Knee"
            )
        players.append(player)
    return players


def add_history(players, season, days):
    """`days` more snapshots for each player, after the ones he has."""
    for player in players:
        newest = player.snapshots.order_by("-as_of").first()
        for day in range(1, days + 1):
            services.record_snapshot(
                player,
                season,
                newest.as_of + dt.timedelta(hours=day),
                salary=newest.salary,
                total_fp=newest.total_fp + day * 10,
                games_played=newest.games_played + day,
                team=player.team,
                position=player.position,
            )


def cost(client, url, **extra):
    with CaptureQueriesContext(connection) as captured:
        response = client.get(url, **extra)
    assert response.status_code == 200
    return len(captured.captured_queries), len(response.content)


HTMX = {"headers": {"HX-Request": "true"}}


# --- constant cost ----------------------------------------------------------------------


@pytest.mark.parametrize("request_kwargs", [{}, HTMX], ids=["full page", "htmx table"])
def test_the_player_list_costs_the_same_for_a_page_of_any_length(signed_in, season, request_kwargs):
    add_players(season, 3)
    small, _ = cost(signed_in, LIST, **request_kwargs)

    add_players(season, 57, start=3)
    large, _ = cost(signed_in, LIST, **request_kwargs)

    assert large == small, f"{small} queries for 3 players, {large} for a full page of 50"


def snapshot_rows_fetched(client, url, **extra):
    """Queries that fetch snapshot rows for the players on the page.

    A prefetch is one query however many rows it brings back, so counting
    queries alone would never have caught the season's worth of snapshots
    the player list used to load. This looks for the prefetch itself.
    """
    with CaptureQueriesContext(connection) as captured:
        client.get(url, **extra)
    return [
        query["sql"]
        for query in captured.captured_queries
        if '"fantasy_playersnapshot"."player_id" IN (' in query["sql"]
    ]


@pytest.mark.parametrize("sort", ["avg", "hotness", "latest", "outlook"])
def test_the_player_list_costs_the_same_however_long_the_season(signed_in, season, sort):
    """No query, and no rows, per snapshot: a season adds one a day per player."""
    players = add_players(season, 10)
    short, _ = cost(signed_in, LIST, data={"sort": sort})

    add_history(players, season, days=20)
    long, _ = cost(signed_in, LIST, data={"sort": sort})

    assert long == short
    assert snapshot_rows_fetched(signed_in, LIST, data={"sort": sort}) == []
    assert snapshot_rows_fetched(signed_in, CHART) == []


def test_the_team_page_reads_no_snapshot_rows(signed_in, season):
    add_players(season, 5, days=10)

    assert snapshot_rows_fetched(signed_in, reverse("nba:team-detail", args=["BOS"])) == []


def test_the_team_page_costs_the_same_for_a_squad_of_any_size(signed_in, season):
    add_players(season, 3)
    url = reverse("nba:team-detail", args=["BOS"])
    small, _ = cost(signed_in, url)

    add_players(season, 15, start=3)
    large, _ = cost(signed_in, url)

    assert large == small


def test_the_chart_costs_the_same_for_a_league_of_any_size(signed_in, season):
    add_players(season, 3)
    small, _ = cost(signed_in, CHART)

    add_players(season, 60, start=3)
    large, _ = cost(signed_in, CHART)

    assert large == small


# --- byte budgets ------------------------------------------------------------------------


@pytest.fixture
def full_page(season):
    return add_players(season, 50)


# Measured with the data above, plus about a quarter. See the module docstring.
BUDGETS_KB = {
    "full page": 420,
    "htmx table": 380,
    "team page": 455,
    "chart": 45,
    "injury modal": 5,
}


def test_the_pages_stay_within_their_budgets(signed_in, full_page):
    sizes = {
        "full page": cost(signed_in, LIST)[1],
        "htmx table": cost(signed_in, LIST, **HTMX)[1],
        "team page": cost(signed_in, reverse("nba:team-detail", args=["BOS"]))[1],
        "chart": cost(signed_in, CHART, **HTMX)[1],
        "injury modal": cost(
            signed_in, reverse("nba:player-injury", args=[full_page[0].slug]), **HTMX
        )[1],
    }

    over = {
        name: f"{size / KB:.0f} KB > {BUDGETS_KB[name]} KB"
        for name, size in sizes.items()
        if size > BUDGETS_KB[name] * KB
    }
    assert not over, over


def test_a_table_row_stays_small(signed_in, season):
    """What one more player adds to the HTMX table. The injury dialog that used
    to ride in every row was 5 KB on its own."""
    add_players(season, 1)
    _, one = cost(signed_in, LIST, **HTMX)

    add_players(season, 49, start=1)
    _, fifty = cost(signed_in, LIST, **HTMX)

    per_row = (fifty - one) / 49
    assert per_row < 7 * KB, f"{per_row / KB:.1f} KB per row"


def test_a_chart_dot_stays_small(signed_in, season):
    """Two circles and a line of JSON; it was about 0.7 KB of attributes."""
    add_players(season, 1)
    _, one = cost(signed_in, CHART, **HTMX)

    add_players(season, 49, start=1)
    _, fifty = cost(signed_in, CHART, **HTMX)

    per_dot = (fifty - one) / 49
    assert per_dot < 0.9 * KB, f"{per_dot:.0f} bytes per dot"
