"""Last season's fantasy points by this game's own scoring formula."""

import importlib
from decimal import Decimal
from types import SimpleNamespace

import pytest

from apps.nba.season_stats import game_fantasy_points, parse_season_stats

from .test_season_stats_parser import HEADER, LUKA, line, stats_file

# Dončić's 2025-26 line, as the parser reads it.
LUKA_VALUES = {
    "points": Decimal("33.5"),
    "rebounds": Decimal("7.7"),
    "assists": Decimal("8.3"),
    "steals": Decimal("1.6"),
    "blocks": Decimal("0.5"),
    "turnovers": Decimal("4.0"),
    "field_goals_made": Decimal("10.8"),
    "field_goals_attempted": Decimal("22.8"),
    "free_throws_made": Decimal("7.9"),
    "free_throws_attempted": Decimal("10.1"),
    "personal_fouls": Decimal("2.4"),
}


def test_doncic_by_hand():
    # 33.5 + 7.7 + 12.45 + 3.2 + 1.0 - 8.0 - 6.0 - 1.1 - 1.2
    assert game_fantasy_points(LUKA_VALUES) == Decimal("41.55")


@pytest.mark.parametrize(
    ("field", "effect"),
    [
        ("points", Decimal("1")),
        ("rebounds", Decimal("1")),
        ("assists", Decimal("1.5")),
        ("steals", Decimal("2")),
        ("blocks", Decimal("2")),
        ("turnovers", Decimal("-2")),
        ("field_goals_attempted", Decimal("-0.5")),
        ("field_goals_made", Decimal("0.5")),
        ("free_throws_attempted", Decimal("-0.5")),
        ("free_throws_made", Decimal("0.5")),
        ("personal_fouls", Decimal("-0.5")),
    ],
)
def test_each_stat_counts_with_its_weight(field, effect):
    """One more of a stat moves the score by its weight in the rules."""
    more = {**LUKA_VALUES, field: LUKA_VALUES[field] + 1}

    assert game_fantasy_points(more) - game_fantasy_points(LUKA_VALUES) == effect


def test_a_miss_is_never_below_zero():
    # Rounded averages: 1.0 made of 0.9 attempted would be a negative miss.
    values = {
        **LUKA_VALUES,
        "free_throws_made": Decimal("1.0"),
        "free_throws_attempted": Decimal("0.9"),
    }
    no_misses = {**values, "free_throws_made": Decimal("0.9")}

    assert game_fantasy_points(values) == game_fantasy_points(no_misses)


def test_a_negative_score_is_kept():
    values = dict.fromkeys(LUKA_VALUES, Decimal("0"))
    values["turnovers"] = Decimal("0.5")

    assert game_fantasy_points(values) == Decimal("-1.00")


def test_the_parser_adds_the_score_to_every_line_whatever_the_games():
    rows = parse_season_stats(stats_file(LUKA, line("Short Season", "BOS", GP=1))).rows

    assert rows[0].values["game_fantasy_points"] == Decimal("41.55")
    assert rows[1].values["game_fantasy_points"] == Decimal("41.55")
    assert rows[1].values["games_played"] == 1


def test_the_migration_uses_the_same_formula():
    migration = importlib.import_module("apps.nba.migrations.0015_game_fantasy_points")

    frozen = migration.game_fantasy_points(SimpleNamespace(**LUKA_VALUES))

    assert frozen == game_fantasy_points(LUKA_VALUES)


def test_a_file_without_tfp_imports():
    header = HEADER.replace("TFP;", "")
    luka = LUKA.replace(";57.7;", ";")

    parsed = parse_season_stats(stats_file(luka, header=header))

    assert parsed.problems == []
    assert parsed.rows[0].values["game_fantasy_points"] == Decimal("41.55")
