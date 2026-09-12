"""The team detail page and the statistics behind it."""

import datetime as dt
import re
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.urls import reverse

from apps.fantasy import services
from apps.fantasy.models import Season
from apps.nba import stats
from apps.nba.models import Player, Team


@pytest.fixture
def teams(db):
    call_command("seed_teams", verbosity=0)
    return {team.abbreviation: team for team in Team.objects.all()}


@pytest.fixture
def season(db):
    return Season.objects.create(
        label="2026-27",
        starts_on=dt.date(2026, 10, 20),
        ends_on=dt.date(2027, 4, 11),
        is_current=True,
    )


@pytest.fixture
def signed_in(client, user, password, db):
    client.login(email=user.email, password=password)
    return client


def make_player(team, last_name, position=Player.Position.GUARD, **kwargs):
    return Player.objects.create(
        first_name="Test", last_name=last_name, position=position, team=team, **kwargs
    )


def price(player, season, salary, total_fp, games_played):
    """Give a player figures the way an import would: through a snapshot."""
    from django.utils import timezone

    services.record_snapshot(
        player,
        season,
        timezone.now(),
        salary=salary,
        total_fp=total_fp,
        games_played=games_played,
        team=player.team,
        position=player.position,
    )
    player.refresh_from_db()
    return player


@pytest.fixture
def lakers(teams, season):
    """Three Lakers with figures that make every statistic distinguishable."""
    team = teams["LAL"]
    price(make_player(team, "Cheap"), season, 1_000_000, "100.00", 10)  # 10.00/game
    price(make_player(team, "Middle"), season, 4_000_000, "300.00", 10)  # 30.00/game
    price(make_player(team, "Dear"), season, 10_000_000, "400.00", 10)  # 40.00/game
    return team


def url(team_or_abbreviation):
    abbreviation = getattr(team_or_abbreviation, "abbreviation", team_or_abbreviation)
    return reverse("nba:team-detail", args=[abbreviation.lower()])


# --- the statistics themselves -----------------------------------------------


def test_summarise_gives_the_five_figures():
    summary = stats.summarise([Decimal("1"), Decimal("2"), Decimal("6")])
    assert summary["n"] == 3
    assert summary["min"] == Decimal("1")
    assert summary["max"] == Decimal("6")
    assert summary["total"] == Decimal("9")
    assert summary["avg"] == Decimal("3.00")
    assert summary["median"] == Decimal("2.00")


def test_the_median_of_an_even_count_is_the_middle_pair():
    summary = stats.summarise([Decimal("1"), Decimal("2"), Decimal("4"), Decimal("5")])
    assert summary["median"] == Decimal("3.00")


def test_the_median_does_not_care_about_the_input_order():
    unsorted = stats.summarise([Decimal("9"), Decimal("1"), Decimal("5")])
    assert unsorted["median"] == Decimal("5.00")
    assert unsorted["min"] == Decimal("1")


def test_a_missing_figure_is_skipped_not_counted_as_zero():
    """The whole reason `n` exists: an unpriced rookie is not a player on 0 $."""
    summary = stats.summarise([Decimal("10"), None, Decimal("20")])
    assert summary["n"] == 2
    assert summary["avg"] == Decimal("15.00")


def test_nothing_to_summarise_gives_no_figures_rather_than_zeros():
    summary = stats.summarise([None, None])
    assert summary["n"] == 0
    assert summary["avg"] is None
    assert summary["total"] is None
    assert summary["min"] is None


def test_the_team_rate_divides_totals_rather_than_averaging_averages(teams, season):
    """A man who played twice must not weigh as much as one who played twenty."""
    team = teams["BOS"]
    players = [
        price(make_player(team, "Starter"), season, 5_000_000, "400.00", 20),  # 20.00/game
        price(make_player(team, "Cameo"), season, 1_000_000, "120.00", 2),  # 60.00/game
    ]
    # The mean of the per-game averages would be 40.00.
    assert stats.summarise(p.current_fp_per_game for p in players)["avg"] == Decimal("40.00")
    # The team actually scored 520 points in 22 games.
    assert stats.scoring_rate(players) == Decimal("23.64")


def test_the_team_rate_is_unknown_before_anyone_has_played(teams, season):
    player = price(make_player(teams["BOS"], "Injured"), season, 1_000_000, "0.00", 0)
    assert player.current_fp_per_game is None
    assert stats.scoring_rate([player]) is None


# --- the cached columns on Player --------------------------------------------


def test_a_snapshot_fills_the_cached_point_columns(teams, season):
    player = price(make_player(teams["LAL"], "Scorer"), season, 3_000_000, "250.00", 10)
    assert player.current_total_fp == Decimal("250.00")
    assert player.current_fp_per_game == Decimal("25.00")
    assert player.current_games_played == 10


def test_a_newer_snapshot_moves_the_cached_points(teams, season):
    from django.utils import timezone

    player = price(make_player(teams["LAL"], "Improving"), season, 3_000_000, "100.00", 10)
    services.record_snapshot(
        player,
        season,
        timezone.now() + dt.timedelta(days=7),
        salary=3_500_000,
        total_fp="300.00",
        games_played=15,
        team=player.team,
        position=player.position,
    )
    player.refresh_from_db()
    assert player.current_total_fp == Decimal("300.00")
    assert player.current_fp_per_game == Decimal("20.00")
    assert player.current_games_played == 15


def test_no_games_played_leaves_the_per_game_column_empty(teams, season):
    """NULL, not zero: he has not scored 0 per game, he has not played."""
    player = price(make_player(teams["LAL"], "Benched"), season, 900_000, "0.00", 0)
    assert player.current_fp_per_game is None
    assert player.current_games_played == 0


def test_syncing_twice_reports_no_further_changes(teams, season):
    player = price(make_player(teams["LAL"], "Steady"), season, 3_000_000, "250.00", 10)
    assert services.sync_player_from_latest_snapshot(player) == []


# --- the page ----------------------------------------------------------------


def test_the_detail_page_requires_login(client, lakers):
    response = client.get(url(lakers))
    assert response.status_code == 302
    assert reverse("accounts:login") in response.url


def test_the_abbreviation_finds_the_team_in_either_case(signed_in, lakers):
    assert signed_in.get("/teams/lal/").status_code == 200
    assert signed_in.get("/teams/LAL/").status_code == 200


def test_an_unknown_abbreviation_is_a_404(signed_in, teams):
    assert signed_in.get("/teams/xyz/").status_code == 404


def test_the_page_names_the_team_and_its_division(signed_in, lakers):
    body = signed_in.get(url(lakers)).content.decode()
    assert "Los Angeles Lakers" in body
    assert "Western Conference" in body
    assert "Pacific" in body


def test_the_page_lists_only_this_team(signed_in, lakers, teams, season):
    price(make_player(teams["BOS"], "Celtic"), season, 2_000_000, "200.00", 10)
    body = signed_in.get(url(lakers)).content.decode()
    assert "Test Cheap" in body
    assert "Test Celtic" not in body


def test_team_page_uses_the_player_table_with_sortable_stats(signed_in, lakers):
    response = signed_in.get(url(lakers))
    body = response.content.decode()

    assert response.context["current_sort"] == "avg"
    assert "Actual salary" in body
    assert "Expected salary" in body
    assert "Difference" in body
    assert "Hotness" in body
    assert 'sort=salary' in body


def test_retired_players_are_out_of_the_statistics_but_counted(signed_in, lakers, season):
    price(make_player(lakers, "Retired", is_active=False), season, 20_000_000, "800.00", 20)
    response = signed_in.get(url(lakers))
    assert len(response.context["players"]) == 3
    assert response.context["inactive_count"] == 1
    body = response.content.decode()
    assert "Test Retired" not in body
    assert "1 inactive" in body
    # The 20m man would have moved the maximum, had he counted.
    assert "$20.00M" not in body


def test_the_salary_statistics_are_right(signed_in, lakers):
    cards = {
        card["title"]: dict(card["rows"]) for card in signed_in.get(url(lakers)).context["cards"]
    }
    salary = cards["Salaries"]
    assert salary["Average"] == Decimal("5000000.00")  # (1 + 4 + 10) / 3
    assert salary["Median"] == Decimal("4000000.00")
    assert salary["Lowest"] == Decimal("1000000.00")
    assert salary["Highest"] == Decimal("10000000.00")
    assert salary["Total"] == Decimal("15000000.00")


def test_the_point_statistics_are_right(signed_in, lakers):
    cards = {
        card["title"]: dict(card["rows"]) for card in signed_in.get(url(lakers)).context["cards"]
    }
    points = cards["Fantasy points"]
    assert points["Avg per game"] == Decimal("26.67")  # (10 + 30 + 40) / 3
    assert points["Median per game"] == Decimal("30.00")
    assert points["Fewest per game"] == Decimal("10.00")
    assert points["Most per game"] == Decimal("40.00")
    assert points["Points total"] == Decimal("800.00")
    assert points["Team per game"] == Decimal("26.67")  # 800 points in 30 games


def test_the_figures_are_shown_in_millions_with_the_exact_amount_on_hover(signed_in, lakers):
    body = signed_in.get(url(lakers)).content.decode()
    assert "$5.00M" in body  # average salary
    assert "$15.00M" in body  # total
    assert 'title="$15,000,000"' in body


def test_the_points_are_shown_with_two_decimals(signed_in, lakers):
    body = signed_in.get(url(lakers)).content.decode()
    assert "26.67" in body
    assert "800.00" in body


def test_the_best_scorer_is_listed_first(signed_in, lakers):
    names = [p.last_name for p in signed_in.get(url(lakers)).context["players"]]
    assert names == ["Dear", "Middle", "Cheap"]


def test_a_player_without_figures_sorts_last_and_shows_no_zeros(signed_in, lakers):
    make_player(lakers, "Unpriced")
    response = signed_in.get(url(lakers))
    assert [p.last_name for p in response.context["players"]][-1] == "Unpriced"
    # Four players, but the statistics are based on the three that have figures.
    cards = {card["title"]: card for card in response.context["cards"]}
    assert cards["Salaries"]["footnote"] == (
        "From 3 of 4 players. No value on record for the rest."
    )
    assert dict(cards["Salaries"]["rows"])["Average"] == Decimal("5000000.00")


def test_a_complete_team_says_nothing_about_coverage(signed_in, lakers):
    cards = signed_in.get(url(lakers)).context["cards"]
    assert all(card["footnote"] == "" for card in cards)


def test_a_team_without_figures_says_so_instead_of_showing_zeros(signed_in, teams):
    make_player(teams["BOS"], "Nobody")
    response = signed_in.get(url(teams["BOS"]))
    body = response.content.decode()
    assert all(card["rows"] == [] for card in response.context["cards"])
    assert "No values yet." in body
    assert "$0.00M" not in body


def test_a_team_without_players_says_so(signed_in, teams):
    body = signed_in.get(url(teams["BOS"])).content.decode()
    assert "No active players on record" in body


# --- getting there -----------------------------------------------------------


def test_the_team_list_links_to_every_team(signed_in, teams):
    body = signed_in.get(reverse("nba:team-list")).content.decode()
    assert f'href="{url("LAL")}"' in body
    # One per franchise. The nav's own link to /teams/ is not one of them.
    assert len(re.findall(r'href="/teams/[a-z]{3}/"', body)) == 30


def test_the_player_list_links_to_the_team(signed_in, lakers):
    body = signed_in.get(reverse("nba:player-list")).content.decode()
    assert f'href="{url("LAL")}"' in body
