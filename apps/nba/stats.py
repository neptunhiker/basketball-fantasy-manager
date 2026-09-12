"""Descriptive statistics over a set of players.

Computed in Python rather than in SQL on purpose. A roster is at most a few
dozen rows and they are already loaded in order to be listed, so aggregating
in the database would mean extra round trips for numbers we can add up here --
and the median is the awkward one: Postgres can do it with `percentile_cont`,
but only through an expression that has to be handed raw SQL.

Everything here treats a missing figure as missing rather than as zero. A
rookie the import has not priced yet has no salary, and averaging him in as if
he earned nothing would be a claim about the team that is simply false.
"""

from decimal import Decimal
from statistics import median

CENTS = Decimal("0.01")


def summarise(values):
    """The five figures, over the values that actually exist.

    `n` is how many players contributed, which is not always the size of the
    team -- callers show it whenever it falls short, so a gap in the data is
    visible instead of quietly moving the average.

    With nothing to summarise every figure is None, never 0: five zeros read as
    measurements, and "this team earns nothing" is not what the data says.
    """
    present = sorted(Decimal(value) for value in values if value is not None)
    if not present:
        return {"n": 0, "total": None, "avg": None, "median": None, "min": None, "max": None}

    total = sum(present, Decimal("0"))
    return {
        "n": len(present),
        "total": total,
        "avg": (total / len(present)).quantize(CENTS),
        "median": Decimal(median(present)).quantize(CENTS),
        "min": present[0],
        "max": present[-1],
    }


def scoring_rate(players):
    """Points per game for a group as a whole: total points over total games.

    Deliberately not the mean of the players' per-game averages, which weights
    a man who played two games the same as one who played twenty. Only players
    with both figures count, so the ratio is always of matching parts.
    """
    pairs = [
        (player.current_total_fp, player.current_games_played)
        for player in players
        if player.current_total_fp is not None and player.current_games_played
    ]
    if not pairs:
        return None

    points = sum((points for points, _ in pairs), Decimal("0"))
    games = sum(games for _, games in pairs)
    return (points / games).quantize(CENTS)
