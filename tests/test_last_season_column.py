"""Last season's fantasy points in the players list, the watchlist and the team page."""

from decimal import Decimal

import pytest
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.fantasy.models import WatchlistEntry
from apps.nba.models import Player, Team
from apps.nba.season_stats import newest_season, short_season

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


def player(name, team=None, **fields):
    first, last = name.split()
    return Player.objects.create(
        first_name=first, last_name=last, position="G", team=team, **fields
    )


@pytest.fixture
def league(teams):
    """Three players with a line last season, one without, one with few games."""
    star = player("Star Player", teams["LAL"])
    solid = player("Solid Player", teams["LAL"])
    bench = player("Bench Player", teams["BOS"])
    rookie = player("Rookie Player", teams["BOS"])
    fluke = player("Fluke Player", teams["BOS"])
    stats_for(star, game_fantasy_points=Decimal("55.0"))
    stats_for(solid, game_fantasy_points=Decimal("40.0"))
    stats_for(bench, game_fantasy_points=Decimal("12.0"))
    stats_for(fluke, game_fantasy_points=Decimal("70.0"), games_played=3)
    return {"star": star, "solid": solid, "bench": bench, "rookie": rookie, "fluke": fluke}


def names(response):
    return [p.first_name for p in response.context["players"]]


# --- helpers -----------------------------------------------------------------------------


def test_newest_season_is_none_without_data(db):
    assert newest_season() is None


def test_newest_season_picks_the_latest_label(league):
    stats_for(league["star"], season="2024-25")

    assert newest_season() == "2025-26"


@pytest.mark.parametrize(
    ("label", "short"), [("2025-26", "25/26"), ("1999-00", "99/00"), (None, "")]
)
def test_short_season(label, short):
    assert short_season(label) == short


# --- sorting -------------------------------------------------------------------------


def test_before_tip_off_the_list_sorts_by_last_season(signed_in, league):
    response = signed_in.get(LIST)

    assert response.context["current_sort"] == "last_fp"
    assert response.context["current_dir"] == "desc"
    assert names(response) == ["Fluke", "Star", "Solid", "Bench", "Rookie"]


def test_once_the_season_has_points_avg_is_the_default_again(signed_in, league):
    Player.objects.filter(pk=league["bench"].pk).update(
        current_fp_per_game=Decimal("20"), current_games_played=3
    )

    assert signed_in.get(LIST).context["current_sort"] == "avg"


def test_an_import_before_tip_off_does_not_end_the_preseason_default(signed_in, league):
    # basketball.de lists everyone at 0.00 points and no games until tip-off.
    Player.objects.update(current_total_fp=Decimal("0.00"), current_games_played=0)

    assert signed_in.get(LIST).context["current_sort"] == "last_fp"


def test_a_chosen_sort_always_wins(signed_in, league):
    response = signed_in.get(LIST, {"sort": "name"})

    assert response.context["current_sort"] == "name"


def test_low_first_keeps_players_without_a_line_last(signed_in, league):
    response = signed_in.get(LIST, {"sort": "last_fp", "dir": "asc"})

    assert names(response) == ["Bench", "Solid", "Star", "Fluke", "Rookie"]


def test_only_the_newest_season_counts(signed_in, league):
    stats_for(league["bench"], season="2024-25", game_fantasy_points=Decimal("99.0"))

    response = signed_in.get(LIST, {"sort": "last_fp"})

    assert names(response)[-2:] == ["Bench", "Rookie"]


def test_without_a_stored_season_there_is_no_column_and_no_sort(signed_in, teams):
    player("Lone Player", teams["LAL"])

    response = signed_in.get(LIST, {"sort": "last_fp"})

    assert response.status_code == 200
    assert response.context["current_sort"] == "avg"
    page = response.content.decode()
    assert "sort=last_fp" not in page
    assert 'name="last_min_games"' not in page


# --- filtering -----------------------------------------------------------------------


def test_the_games_filter_drops_short_seasons_and_missing_lines(signed_in, league):
    response = signed_in.get(LIST, {"last_min_games": "1"})

    assert names(response) == ["Star", "Solid", "Bench"]
    assert response.context["active_filter_count"] == 1


def test_the_filter_is_offered_with_the_season_in_its_label(signed_in, league):
    page = signed_in.get(LIST).content.decode()

    assert 'name="last_min_games"' in page
    assert "At least 20 games in 25/26" in page


def test_an_empty_filtered_list_says_nothing_matches(signed_in, league):
    response = signed_in.get(LIST, {"last_min_games": "1", "q": "nobody"})

    assert "Nothing matches those filters." in response.content.decode()


# --- the column ----------------------------------------------------------------------


def test_the_column_shows_values_games_and_gaps(signed_in, league):
    page = signed_in.get(LIST).content.decode()

    assert "FP 25/26" in page
    assert ">55.0<" in page
    assert "3 G" in page, "a short season shows its game count"
    assert "Only 3 games last season" in page
    assert "fantasy points per game last season (NBA.com)." in page


def test_the_watchlist_has_the_column_too(signed_in, user, league):
    WatchlistEntry.objects.create(user=user, player=league["solid"])

    response = signed_in.get(reverse("nba:watchlist"))

    assert "FP 25/26" in response.content.decode()
    assert names(response) == ["Solid"]


def test_the_team_page_shows_and_sorts_by_last_season(signed_in, league):
    response = signed_in.get(reverse("nba:team-detail", args=["bos"]), {"sort": "last_fp"})

    assert "FP 25/26" in response.content.decode()
    assert [p.first_name for p in response.context["players"]] == ["Fluke", "Bench", "Rookie"]


def test_german_shows_a_decimal_comma(signed_in, user, league):
    user.language = "de"
    user.save(update_fields=["language"])

    page = signed_in.get(LIST).content.decode()

    assert ">55,0<" in page
    assert "Mindestens 20 Spiele in 25/26" in page


def test_the_list_costs_the_same_however_many_lines_are_stored(signed_in, teams):
    for number in range(5):
        stats_for(player(f"Few P{number}", teams["LAL"]))
    with CaptureQueriesContext(connection) as few:
        signed_in.get(LIST)

    for number in range(45):
        stats_for(player(f"Many P{number}", teams["BOS"]))
    with CaptureQueriesContext(connection) as many:
        signed_in.get(LIST)

    assert len(many) == len(few)
