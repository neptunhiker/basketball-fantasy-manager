"""The salary chart's second view: last season's fantasy points against salary."""

from decimal import Decimal

import pytest
from django.core.management import call_command
from django.template.loader import render_to_string
from django.urls import reverse

from apps.nba.charts import NEON_GREEN, NEON_RED
from apps.nba.models import Player, Team, predict_salary
from apps.nba.season_stats import with_last_season
from apps.nba.views import salary_charts, with_expected_salary

from .test_player_season_stats import stats_for

LIST = reverse("nba:player-list")


@pytest.fixture
def teams(db):
    call_command("seed_teams", verbosity=0)
    return {team.abbreviation: team for team in Team.objects.all()}


@pytest.fixture
def signed_in(client, user, password):
    client.login(email=user.email, password=password)
    return client


def player(name, team, salary=None, **fields):
    first, last = name.split()
    return Player.objects.create(
        first_name=first,
        last_name=last,
        position="G",
        team=team,
        current_salary=salary,
        **fields,
    )


@pytest.fixture
def league(teams):
    star = player("Star Player", teams["LAL"], Decimal("12000000"))
    short = player("Short Season", teams["LAL"], Decimal("3000000"))
    rookie = player("Rookie Player", teams["BOS"], Decimal("1500000"))
    unpriced = player("Unpriced Player", teams["BOS"])
    stats_for(star, game_fantasy_points=Decimal("40.00"))
    stats_for(short, game_fantasy_points=Decimal("22.50"), games_played=4)
    stats_for(unpriced, game_fantasy_points=Decimal("30.00"))
    return {"star": star, "short": short, "rookie": rookie, "unpriced": unpriced}


def charts_for(season="2025-26"):
    players = with_expected_salary(with_last_season(Player.objects.all(), season))
    return salary_charts(players, season)


def dot(chart, label):
    return next(d for d in chart["dots"] if d["label"] == label)


# --- the chart data ----------------------------------------------------------------------


def test_last_season_points_against_this_seasons_salary(league):
    star = dot(charts_for()["last_season_chart"], "Star Player")

    assert star["x_value"] == Decimal("40.00")
    assert star["y_value"] == Decimal("12")
    assert star["expected_salary"] == predict_salary(Decimal("40.00"))
    assert star["difference"] == predict_salary(Decimal("40.00")) - 12
    assert star["games_played"] == 64


def test_only_players_with_a_line_and_a_salary_are_plotted(league):
    labels = {d["label"] for d in charts_for()["last_season_chart"]["dots"]}

    assert labels == {"Star Player", "Short Season"}


def test_dots_are_green_from_six_games(league):
    chart = charts_for()["last_season_chart"]

    assert dot(chart, "Star Player")["color"] == NEON_GREEN
    assert dot(chart, "Short Season")["color"] == NEON_RED


def test_without_a_stored_season_there_is_no_second_chart(teams):
    player("Lone Player", teams["LAL"], Decimal("1000000"))

    assert charts_for(season=None)["last_season_chart"] is None


# --- on the page -------------------------------------------------------------------------


def test_before_tip_off_the_chart_opens_on_last_season(signed_in, league):
    response = signed_in.get(LIST)

    assert response.context["default_chart_view"] == "last"
    page = response.content.decode()
    assert "chartView: 'last'" in page
    assert "Fantasy points 25/26 and salary" in page


def test_once_games_are_played_it_opens_on_this_season(signed_in, league):
    Player.objects.filter(pk=league["star"].pk).update(
        current_games_played=2, current_fp_per_game=Decimal("30")
    )

    assert signed_in.get(LIST).context["default_chart_view"] == "current"


def test_the_toggle_offers_both_seasons(signed_in, league):
    page = signed_in.get(LIST).content.decode()

    assert 'aria-label="Season shown in the chart"' in page
    assert "This season" in page
    assert ">25/26</button>" in page


def test_without_a_stored_season_there_is_no_toggle(signed_in, teams):
    player("Lone Player", teams["LAL"], Decimal("1000000"))

    response = signed_in.get(LIST)

    assert response.context["default_chart_view"] == "current"
    assert "Season shown in the chart" not in response.content.decode()


def test_the_team_page_has_the_toggle_too(signed_in, league):
    response = signed_in.get(reverse("nba:team-detail", args=["lal"]))

    assert "Season shown in the chart" in response.content.decode()
    assert len(response.context["last_season_chart"]["dots"]) == 2


def test_the_last_season_details_leave_out_this_seasons_rows(league):
    chart = charts_for()["last_season_chart"]
    context = {"chart": chart, "x_axis_label": "Fantasy points per game 25/26"}

    last = render_to_string("nba/partials/scatter_chart.html", {**context, "variant": "last"})
    current = render_to_string("nba/partials/scatter_chart.html", context)

    assert "Fantasy points per game 25/26</dt>" in last
    assert "Hotness" not in last
    assert ">Points</dt>" not in last
    assert "Hotness" in current
    assert ">Points</dt>" in current
