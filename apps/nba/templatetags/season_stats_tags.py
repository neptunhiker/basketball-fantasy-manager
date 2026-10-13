"""The official season stats card on the player page.

An inclusion tag rather than view context, so any page that shows a player can
show the card with one line: `{% player_season_stats player %}`.
"""

from django import template
from django.utils.translation import gettext_lazy

from apps.nba.models import PlayerSeasonStats

register = template.Library()

# Below this many games a per-game average says little, and a player who
# appeared twice could top every ranking. Ranks only count players above it.
MIN_GAMES_FOR_RANK = 20

# The figures that get a league rank on the card, best first.
RANKED = ("fantasy_points", "points", "rebounds", "assists")


@register.inclusion_tag("nba/partials/season_stats_card.html")
def player_season_stats(player):
    """The player's newest official season, with ranks among regulars."""
    stats = player.season_stats.order_by("-season").first()
    if stats is None:
        # Say which season is missing only when some season exists at all;
        # before the first upload the card stays out of the way.
        newest = (
            PlayerSeasonStats.objects.order_by("-season").values_list("season", flat=True).first()
        )
        return {"stats": None, "newest_season": newest}

    ranks = {}
    regulars = None
    if stats.games_played >= MIN_GAMES_FOR_RANK:
        peers = PlayerSeasonStats.objects.filter(
            season=stats.season, games_played__gte=MIN_GAMES_FOR_RANK
        )
        regulars = peers.count()
        ranks = {
            field: peers.filter(**{f"{field}__gt": getattr(stats, field)}).count() + 1
            for field in RANKED
        }

    team = player.team.abbreviation if player.team else None
    return {
        "stats": stats,
        "ranks": ranks,
        "regulars": regulars,
        "min_games": MIN_GAMES_FOR_RANK,
        "few_games": stats.games_played < MIN_GAMES_FOR_RANK,
        "moved": bool(stats.team_abbreviation) and team != stats.team_abbreviation,
        "current_team": team,
        "shooting": [
            {
                "label": gettext_lazy("Field goals"),
                "made": stats.field_goals_made,
                "attempted": stats.field_goals_attempted,
                "pct": stats.field_goal_pct,
            },
            {
                "label": gettext_lazy("3-pointers"),
                "made": stats.threes_made,
                "attempted": stats.threes_attempted,
                "pct": stats.three_pct,
            },
            {
                "label": gettext_lazy("Free throws"),
                "made": stats.free_throws_made,
                "attempted": stats.free_throws_attempted,
                "pct": stats.free_throw_pct,
            },
        ],
    }
