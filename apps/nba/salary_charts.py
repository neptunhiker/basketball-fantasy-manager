"""The salary scatter charts: points per game against salary, per player.

Kept out of the views so every page that plots players -- the player lists,
a team, a roster's build page -- draws them from one function, and the
fantasy views can use it without importing the NBA views, which import them.
"""

from django.urls import reverse

from . import charts
from .models import predict_salary


def salary_charts(players, last_season):
    """The two salary scatter charts for a list of players.

    `salary_fp_chart`: this season's points per game against salary.
    `last_season_chart`: last season's fantasy points per game against this
    season's salary -- there is no salary for last season, and the question
    is who is cheap now for what he produced then. Expected salary and Value
    use the same salary formula, which is fair because last season's points
    are worked out by this game's rules. None while no season is stored.

    Both draw the salary formula as the fair-value curve: a dot below it is a
    player paid less than his points are worth.
    """
    players = list(players)
    return {
        "salary_fp_chart": current_salary_chart(players),
        "last_season_chart": last_season_salary_chart(players) if last_season else None,
    }


def current_salary_chart(players):
    """This season's points per game against salary; see `salary_charts`."""
    points = (
        (
            player.full_name,
            player.current_fp_per_game,
            _millions(player.current_salary),
            player.predicted_salary,
            {
                "detail_url": reverse("nba:player-detail", args=[player.slug]),
                "total_fp": player.current_total_fp,
                "games_played": player.current_games_played,
                "difference": _difference(player.predicted_salary, player.current_salary),
                "hotness": player.hotness_score(),
            },
        )
        for player in players
    )
    return charts.scatter_chart(points, curve=predict_salary)


def last_season_salary_chart(players):
    """Last season's points per game against this season's salary.

    Reads `last_season_fp` and `last_season_games`, which
    `apps.nba.season_stats.with_last_season` annotates.
    """
    points = []
    for player in players:
        fp = getattr(player, "last_season_fp", None)
        expected = predict_salary(fp) if fp is not None else None
        points.append(
            (
                player.full_name,
                fp,
                _millions(player.current_salary),
                expected,
                {
                    "detail_url": reverse("nba:player-detail", args=[player.slug]),
                    "games_played": getattr(player, "last_season_games", None),
                    "difference": _difference(expected, player.current_salary),
                },
            )
        )
    return charts.scatter_chart(points, curve=predict_salary)


def _millions(amount):
    return amount / 1_000_000 if amount is not None else None


def _difference(expected_millions, salary):
    if expected_millions is None or salary is None:
        return None
    return expected_millions - salary / 1_000_000
