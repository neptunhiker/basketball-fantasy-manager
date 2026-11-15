"""Each player's games this season, read off the daily snapshots.

A snapshot holds a player's season total and games played. The import runs
once a day after the US night games, so the difference between two
consecutive snapshots is that day's game -- a game log, without importing box
scores. A skipped day merges two games into one entry; totals and per-game
averages stay right.

The first snapshot of each player is the baseline, never a game: for a player
who arrived mid-season it holds games played elsewhere. A difference with
fewer games than before, or with points but no game, is a corrected import
and is left out rather than guessed at. A real game can be worth negative
points, so the points alone never rule an entry out.

Each entry keeps the team of its snapshot, so a window can ask what a player
did for one team only (a teammate's games since an injury).

One query for any number of players. A read model: nothing here writes.
"""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from apps.fantasy.models import PlayerSnapshot

WEEK = dt.timedelta(days=7)


@dataclass(frozen=True)
class Entry:
    """What one snapshot added: the games since the one before, and their points."""

    at: dt.datetime
    games: int
    fp: Decimal
    team_id: int | None


@dataclass(frozen=True)
class Form:
    """Points and games over a stretch of days."""

    fp: Decimal
    games: int

    @property
    def per_game(self):
        return self.fp / self.games if self.games else None


NO_GAMES = Form(Decimal(0), 0)


class GameLog:
    """The game log of every player in `player_ids` for `season`, up to `until`."""

    def __init__(self, season, player_ids, until=None):
        self._entries = {}
        snapshots = PlayerSnapshot.objects.filter(season=season, player_id__in=set(player_ids))
        if until is not None:
            snapshots = snapshots.filter(as_of__lte=until)
        previous = {}
        for player_id, at, total, games, team_id in snapshots.order_by(
            "player_id", "as_of"
        ).values_list("player_id", "as_of", "total_fp", "games_played", "team_id"):
            before = previous.get(player_id)
            previous[player_id] = (total, games)
            if before is None:
                continue
            played = games - before[1]
            if played <= 0:
                continue
            self._entries.setdefault(player_id, []).append(
                Entry(at=at, games=played, fp=total - before[0], team_id=team_id)
            )

    def entries(self, player_id):
        return self._entries.get(player_id, [])

    def window(self, player_id, start=None, end=None, team_id=None):
        """His points and games after `start` up to and including `end`.

        Only games for `team_id` when one is given. Open-ended without
        `start` or `end`.
        """
        fp, games = Decimal(0), 0
        for entry in self.entries(player_id):
            if start is not None and entry.at <= start:
                continue
            if end is not None and entry.at > end:
                continue
            if team_id is not None and entry.team_id != team_id:
                continue
            fp += entry.fp
            games += entry.games
        return Form(fp, games)

    def weekly(self, player_id, end, weeks=3):
        """His form in each of the `weeks` weeks up to `end`, oldest week first."""
        return [
            self.window(player_id, end - WEEK * (back + 1), end - WEEK * back)
            for back in reversed(range(weeks))
        ]

    def recent(self, player_id, end, days=21):
        """His form over the `days` days up to `end`."""
        return self.window(player_id, end - dt.timedelta(days=days), end)
