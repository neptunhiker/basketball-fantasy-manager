"""Where each team stands in its season: games played, left, coming up.

Read off the stored schedule and the clock, never off results -- the schedule
carries none. One query for all thirty teams: a season is some 1,230 games,
few enough to sort out in Python instead of a dozen aggregate subqueries.
"""

import datetime as dt
from dataclasses import dataclass

from django.utils import timezone

from apps.fantasy.models import Season

from .models import GAME_LENGTH, REGULAR_SEASON_GAMES, Game

# What "the next days" means on the team pages.
NEXT_DAYS = 7
# How many games the team page lists before "Show full season".
UPCOMING_GAMES = 10


@dataclass
class TeamSchedule:
    """One team's season so far. `left` is counted from the 82, not the
    stored games: the NBA Cup's last two games are only scheduled in December."""

    team_id: object
    played: int = 0
    next_days: int = 0
    next_game: Game | None = None
    now: dt.datetime | None = None

    @property
    def left(self):
        return max(REGULAR_SEASON_GAMES - self.played, 0)

    @property
    def progress(self):
        """Share of the season played, in whole percent, for the progress bar."""
        return min(round(100 * self.played / REGULAR_SEASON_GAMES), 100)

    @property
    def next_game_is_live(self):
        return self.next_game is not None and self.next_game.tip_off <= self.now

    @property
    def next_game_is_soon(self):
        """Within six days: a weekday alone names it without doubt."""
        return self.next_game is not None and self.next_game.tip_off < self.now + dt.timedelta(
            days=6
        )

    @property
    def next_opponent(self):
        return self.next_game.opponent_of_id(self.team_id) if self.next_game else None

    @property
    def next_game_is_home(self):
        return self.next_game is not None and self.next_game.home_team_id == self.team_id


def season_games(season=None):
    """The current season's games that count, or none without a season."""
    season = season or Season.objects.filter(is_current=True).first()
    if season is None:
        return Game.objects.none()
    return (
        Game.objects.filter(season_year=season.ends_on.year)
        .counting()
        .select_related("home_team", "away_team")
        .order_by("tip_off", "espn_id")
    )


def team_schedules(now=None, games=None):
    """{team id: TeamSchedule} for every team with a game this season.

    Empty when no schedule has been loaded -- the pages then show a dash,
    not a season of 82 games left.
    """
    now = now or timezone.now()
    played_before = now - GAME_LENGTH
    horizon = now + dt.timedelta(days=NEXT_DAYS)
    schedules = {}
    for game in games if games is not None else season_games():
        for team_id in (game.home_team_id, game.away_team_id):
            entry = schedules.get(team_id)
            if entry is None:
                entry = schedules[team_id] = TeamSchedule(team_id=team_id, now=now)
            if game.tip_off <= played_before:
                entry.played += 1
                continue
            if entry.next_game is None:
                entry.next_game = game
            if game.tip_off < horizon:
                entry.next_days += 1
    return schedules
