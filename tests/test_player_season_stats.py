"""The official season stats card on the player page."""

from decimal import Decimal

import pytest
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.nba.models import Player, PlayerSeasonStats, Team
from apps.nba.templatetags.season_stats_tags import MIN_GAMES_FOR_RANK, player_season_stats

STAT_DEFAULTS = {
    "age": 27,
    "games_played": 64,
    "wins": 43,
    "losses": 21,
    "minutes": Decimal("35.8"),
    "points": Decimal("33.5"),
    "field_goals_made": Decimal("10.8"),
    "field_goals_attempted": Decimal("22.8"),
    "field_goal_pct": Decimal("47.6"),
    "threes_made": Decimal("4.0"),
    "threes_attempted": Decimal("10.8"),
    "three_pct": Decimal("36.6"),
    "free_throws_made": Decimal("7.9"),
    "free_throws_attempted": Decimal("10.1"),
    "free_throw_pct": Decimal("78.0"),
    "offensive_rebounds": Decimal("0.6"),
    "defensive_rebounds": Decimal("7.1"),
    "rebounds": Decimal("7.7"),
    "assists": Decimal("8.3"),
    "turnovers": Decimal("4.0"),
    "steals": Decimal("1.6"),
    "blocks": Decimal("0.5"),
    "personal_fouls": Decimal("2.4"),
    "fantasy_points": Decimal("57.7"),
}


@pytest.fixture
def teams(db):
    call_command("seed_teams", verbosity=0)
    return {team.abbreviation: team for team in Team.objects.all()}


@pytest.fixture
def luka(teams):
    return Player.objects.create(
        first_name="Luka", last_name="Dončić", position="G", team=teams["LAL"]
    )


def stats_for(player, season="2025-26", team="LAL", **fields):
    return PlayerSeasonStats.objects.create(
        player=player,
        season=season,
        source_name=player.full_name,
        team_abbreviation=team,
        **{**STAT_DEFAULTS, **fields},
    )


def someone(name, **fields):
    first, last = name.split()
    player = Player.objects.create(first_name=first, last_name=last, position="F")
    return stats_for(player, team="", **fields)


# --- the tag -----------------------------------------------------------------------------


def test_nothing_shows_before_any_season_is_stored(luka):
    assert player_season_stats(luka) == {"stats": None, "newest_season": None}


def test_a_player_without_a_line_names_the_missing_season(luka):
    someone("Other Player")

    assert player_season_stats(luka) == {"stats": None, "newest_season": "2025-26"}


def test_the_newest_season_is_shown(luka):
    stats_for(luka, season="2024-25", points=Decimal("28.1"))
    newest = stats_for(luka)

    assert player_season_stats(luka)["stats"] == newest


def test_ranks_count_regulars_with_more(luka):
    stats_for(luka)
    someone("Better Scorer", points=Decimal("40.0"), fantasy_points=Decimal("50.0"))
    someone("Fluke Night", games_played=2, points=Decimal("60.0"), fantasy_points=Decimal("90.0"))

    card = player_season_stats(luka)

    assert card["ranks"]["points"] == 2
    assert card["ranks"]["fantasy_points"] == 1, "two games do not outrank a season"
    assert card["regulars"] == 2
    assert card["few_games"] is False


def test_ties_share_a_rank(luka):
    stats_for(luka)
    someone("Same Rebounder")

    assert player_season_stats(luka)["ranks"]["rebounds"] == 1


def test_a_player_with_few_games_is_not_ranked(luka):
    stats_for(luka, games_played=MIN_GAMES_FOR_RANK - 1)

    card = player_season_stats(luka)

    assert card["ranks"] == {}
    assert card["few_games"] is True


def test_a_move_since_last_season_is_noticed(luka, teams):
    stats_for(luka, team="DAL")

    card = player_season_stats(luka)

    assert card["moved"] is True
    assert card["current_team"] == "LAL"


def test_staying_put_is_not_a_move(luka):
    stats_for(luka)

    assert player_season_stats(luka)["moved"] is False


# --- on the player page ------------------------------------------------------------------


@pytest.fixture
def signed_in(client, user, password):
    client.login(email=user.email, password=password)
    return client


def page_of(client, player):
    return client.get(reverse("nba:player-detail", args=[player.slug])).content.decode()


def test_the_card_shows_on_the_player_page(signed_in, luka):
    stats_for(luka, team="DAL")

    page = page_of(signed_in, luka)

    assert 'id="season-stats"' in page
    assert "Season 2025-26" in page
    assert "57.7" in page
    assert "47.6%" in page
    assert "10.8 of 22.8 per game" in page
    assert "43\u201321" in page
    assert "DAL" in page and "LAL" in page


def test_the_card_warns_about_few_games(signed_in, luka):
    stats_for(luka, games_played=3)

    assert "Only 3 games played" in page_of(signed_in, luka)


def test_a_player_without_stats_gets_a_quiet_note(signed_in, luka):
    someone("Other Player")

    assert "No official stats for 2025-26" in page_of(signed_in, luka)


def test_no_card_before_the_first_upload(signed_in, luka):
    assert 'id="season-stats"' not in page_of(signed_in, luka)


def test_german_numbers_use_a_decimal_comma(signed_in, user, luka):
    user.language = "de"
    user.save(update_fields=["language"])
    stats_for(luka)

    page = page_of(signed_in, luka)

    assert "Saison 2025-26" in page
    assert "33,5" in page


def test_the_card_costs_the_same_however_many_players_are_ranked(signed_in, luka):
    stats_for(luka)
    someone("Player Zero")
    with CaptureQueriesContext(connection) as few:
        page_of(signed_in, luka)

    for number in range(30):
        someone(f"Player N{number}", points=Decimal(number))
    with CaptureQueriesContext(connection) as many:
        page_of(signed_in, luka)

    assert len(many) == len(few)


def test_the_card_starts_collapsed(signed_in, luka):
    stats_for(luka)

    page = page_of(signed_in, luka)

    card = page[page.index('id="season-stats"') :]
    details = card[card.index("<details") : card.index(">", card.index("<details"))]
    assert " open" not in details
