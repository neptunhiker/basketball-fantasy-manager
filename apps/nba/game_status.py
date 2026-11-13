"""Played, no game or missed: what happened between two snapshots.

A snapshot only carries season totals, so a row whose points did not move can
mean two different things -- his team had no game, or it played without him.
This module tells them apart by laying the stored ESPN schedule over the
snapshots, and adds the injury report that was current when he sat out.

Every screen reads the same `GameStatus`; no template works anything out. The
schedule is loaded once per request (a season is some 1,230 games) and looked
up by bisecting, so a page costs the same whether it shows 3 rows or 300.

A game belongs to the first snapshot read after it was over and counted:
`tip_off + GAME_LENGTH + SOURCE_DELAY <= as_of`. When the games count still
rises by more than the team played -- a game counted late -- the extra games
are taken as played. A late count must never read as a missed game.
"""

import bisect
import datetime as dt
import itertools
from dataclasses import dataclass, field
from types import SimpleNamespace

from django.db.models.functions import ExtractYear

from .models import (
    DAY_TO_DAY,
    GAME_LENGTH,
    HEALTHY,
    INJURED,
    SOURCE_DELAY,
    Game,
    PlayerInjury,
    injury_state,
)

PLAYED = "played"
PARTLY = "partly"
MISSED = "missed"
NO_GAME = "no_game"
NO_TEAM = "no_team"
SEASON_START = "season_start"
CORRECTED = "corrected"

# Nothing happened on the court that involved him: the screens grey these out.
QUIET = frozenset({NO_GAME, NO_TEAM, SEASON_START, CORRECTED})

# One reading frame before any real game, for "everything up to the first snapshot".
_DAWN = dt.datetime(1900, 1, 1, tzinfo=dt.UTC)


def settled_at(game):
    """When a game's numbers can be expected in a snapshot."""
    return game.tip_off + GAME_LENGTH + SOURCE_DELAY


# --- the schedule -------------------------------------------------------------


class TeamGames:
    """One team's counting games, ordered by when they settle."""

    def __init__(self):
        self._settled = []
        self._games = []

    def add(self, game):
        self._settled.append(settled_at(game))
        self._games.append(game)

    def sort(self):
        pairs = sorted(zip(self._settled, self._games, strict=True), key=lambda pair: pair[0])
        self._settled = [settled for settled, _ in pairs]
        self._games = [game for _, game in pairs]

    def between(self, start, end):
        """Games settled after `start` and by `end`."""
        low = bisect.bisect_right(self._settled, start)
        high = bisect.bisect_right(self._settled, end)
        return self._games[low:high]

    def last_before(self, moment, count):
        """The `count` newest games settled by `moment`, oldest first."""
        high = bisect.bisect_right(self._settled, moment)
        return self._games[max(high - count, 0) : high]


class Schedule:
    """{team id: TeamGames} over the games given, with an empty fallback."""

    _EMPTY = TeamGames()

    def __init__(self, games):
        self._teams = {}
        for game in games:
            for team_id in (game.home_team_id, game.away_team_id):
                self._teams.setdefault(team_id, TeamGames()).add(game)
        for team_games in self._teams.values():
            team_games.sort()

    def __bool__(self):
        return bool(self._teams)

    def team(self, team_id):
        return self._teams.get(team_id, self._EMPTY)

    def between(self, team_ids, start, end):
        games = {
            game.pk: game for team_id in team_ids for game in self.team(team_id).between(start, end)
        }
        return sorted(games.values(), key=lambda game: game.tip_off)


def schedule_for_seasons(season_ids):
    """The counting games of the given seasons, in one query."""
    from apps.fantasy.models import Season

    years = (
        Season.objects.filter(pk__in=season_ids)
        .annotate(year=ExtractYear("ends_on"))
        .values("year")
    )
    games = (
        Game.objects.filter(season_year__in=years)
        .counting()
        .select_related("home_team", "away_team")
    )
    return Schedule(games)


# --- injuries -----------------------------------------------------------------


class InjuryHistory:
    """Each player's injury reports in time order, to ask "what was it then?"."""

    def __init__(self, reports):
        self._players = {}
        for report in sorted(reports, key=lambda report: (report.observed_at, report.created_at)):
            stamps, items = self._players.setdefault(report.player_id, ([], []))
            stamps.append(report.observed_at)
            items.append(report)

    def at(self, player_id, moment):
        """The newest report observed by `moment`, or None."""
        stamps, items = self._players.get(player_id, ((), ()))
        index = bisect.bisect_right(stamps, moment)
        return items[index - 1] if index else None


def injury_history(player_ids, until=None):
    reports = PlayerInjury.objects.filter(player_id__in=player_ids).only(
        "player_id",
        "observed_at",
        "created_at",
        "status",
        "injury_location",
        "injury_type",
        "return_date",
    )
    if until is not None:
        reports = reports.filter(observed_at__lte=until)
    return InjuryHistory(reports)


# --- the status ---------------------------------------------------------------


@dataclass(frozen=True)
class Opponent:
    game: Game
    team: object
    is_home: bool


@dataclass(frozen=True)
class GameStatus:
    """What happened between two snapshots, for one player."""

    kind: str
    games: tuple = ()
    played: int = 0
    team_ids: frozenset = field(default_factory=frozenset)
    # The report current at tip-off of the last game he missed -- only kept
    # when it says he was hurt; a "Healthy" report is no reason to sit out.
    injury: object = None

    @property
    def missed(self):
        return max(len(self.games) - self.played, 0) if self.kind in (MISSED, PARTLY) else 0

    @property
    def is_quiet(self):
        return self.kind in QUIET

    @property
    def is_played(self):
        return self.kind == PLAYED

    @property
    def is_partly(self):
        return self.kind == PARTLY

    @property
    def is_missed(self):
        return self.kind == MISSED

    @property
    def is_no_game(self):
        return self.kind == NO_GAME

    @property
    def game_count(self):
        """Games he played (played) or his team played (missed, partly)."""
        return self.played if self.kind == PLAYED else len(self.games)

    @property
    def opponents(self):
        result = []
        for game in self.games:
            is_home = game.home_team_id in self.team_ids
            result.append(
                Opponent(
                    game=game, team=game.away_team if is_home else game.home_team, is_home=is_home
                )
            )
        return result

    @property
    def injury_state(self):
        return injury_state(self.injury.status) if self.injury else None

    @property
    def is_injured(self):
        return self.injury_state == INJURED

    @property
    def is_day_to_day(self):
        return self.injury_state == DAY_TO_DAY


def _snapshot_view(snapshot):
    return SimpleNamespace(
        as_of=snapshot.as_of,
        team_id=snapshot.team_id,
        games_played=snapshot.games_played,
        season_id=snapshot.season_id,
    )


def status_between(newer, older, schedule, injuries=None, player_id=None):
    """The `GameStatus` for the window from `older` to `newer`.

    Both are snapshot-like (`as_of`, `team_id`, `games_played`, `season_id`).
    """
    if older is None or older.season_id != newer.season_id:
        return GameStatus(SEASON_START)
    gained = newer.games_played - older.games_played
    if gained < 0:
        return GameStatus(CORRECTED)
    team_ids = frozenset(team_id for team_id in (older.team_id, newer.team_id) if team_id)
    if not team_ids:
        return GameStatus(PLAYED, played=gained) if gained else GameStatus(NO_TEAM)

    games = schedule.between(team_ids, older.as_of, newer.as_of)
    if gained == 0:
        if not games:
            return GameStatus(NO_GAME, team_ids=team_ids)
        return GameStatus(
            MISSED,
            games=tuple(games),
            team_ids=team_ids,
            injury=_hurt_at(injuries, player_id, games[-1].tip_off),
        )
    if gained < len(games):
        return GameStatus(
            PARTLY,
            games=tuple(games),
            played=gained,
            team_ids=team_ids,
            injury=_hurt_at(injuries, player_id, games[-1].tip_off),
        )
    if gained > len(games):
        # Counted late: the games before the window that the source had not
        # yet taken in, so the row can still name its opponents.
        late = [
            game
            for team_id in team_ids
            for game in schedule.team(team_id).last_before(older.as_of, gained - len(games))
        ]
        late = sorted({game.pk: game for game in late}.values(), key=lambda game: game.tip_off)
        games = (late[-(gained - len(games)) :] if late else []) + games
    return GameStatus(PLAYED, games=tuple(games), played=gained, team_ids=team_ids)


def _hurt_at(injuries, player_id, moment):
    if injuries is None:
        return None
    report = injuries.at(player_id, moment)
    if report is None or injury_state(report.status) == HEALTHY:
        return None
    return report


# --- the screens --------------------------------------------------------------


@dataclass
class SeasonSummary:
    """Games played against team games, for the summary above the snapshots."""

    played: int
    team_games: int
    injured: int = 0
    day_to_day: int = 0
    unreported: int = 0

    @property
    def missed(self):
        return self.team_games - self.played

    @property
    def played_share(self):
        """Width of the green part of the bar, in whole percent."""
        return round(100 * self.played / self.team_games) if self.team_games else 0

    @property
    def has_breakdown(self):
        """Whether the rows account for every missed game (see `snapshot_history`)."""
        return bool(self.missed) and self.injured + self.day_to_day + self.unreported == self.missed


def snapshot_history(player, snapshots):
    """`(statuses, summary)` for the player page.

    `statuses` holds one `GameStatus` per snapshot, in the order given (newest
    first). `summary` covers the newest snapshot's season, or is None before
    his teams have played: it is counted from season totals rather than by
    adding up rows -- the newest games count is the truth, and the schedule
    up to that read is what his teams played. The DNP breakdown by injury
    only counts when the rows add up to the same number of missed games.
    """
    if not snapshots:
        return [], None
    schedule = schedule_for_seasons({snapshot.season_id for snapshot in snapshots})
    injuries = injury_history([player.pk], until=snapshots[0].as_of)
    views = [_snapshot_view(snapshot) for snapshot in snapshots]
    statuses = [
        status_between(
            view,
            views[index + 1] if index + 1 < len(views) else None,
            schedule,
            injuries,
            player.pk,
        )
        for index, view in enumerate(views)
    ]

    season = [
        (view, status)
        for view, status in zip(views, statuses, strict=True)
        if view.season_id == views[0].season_id
    ]
    oldest = season[-1][0]
    team_games = len(schedule.between({oldest.team_id} - {None}, _DAWN, oldest.as_of))
    for (newer, _), (older, _) in itertools.pairwise(season):
        team_ids = {older.team_id, newer.team_id} - {None}
        team_games += len(schedule.between(team_ids, older.as_of, newer.as_of))
    played = views[0].games_played
    # A schedule loaded late or short must not report more games than played.
    team_games = max(team_games, played)
    if not team_games:
        return statuses, None
    summary = SeasonSummary(played=played, team_games=team_games)
    for _, status in season:
        if status.is_injured:
            summary.injured += status.missed
        elif status.is_day_to_day:
            summary.day_to_day += status.missed
        else:
            summary.unreported += status.missed
    return statuses, summary


def attach_latest_status(players):
    """Set `player.latest_status` for the newest window of each player.

    Reads prefetched snapshots when the list brought them (the rosters), and
    otherwise the annotations of `apps.nba.views.with_latest_fp_change`. Two
    queries for the whole list: the schedule and the injury reports.
    """
    players = list(players)
    windows = {}
    for player in players:
        prefetched = getattr(player, "_prefetched_objects_cache", {}).get("snapshots")
        if prefetched is not None:
            ordered = sorted(prefetched, key=lambda snapshot: snapshot.as_of, reverse=True)
            if len(ordered) >= 2:
                windows[player.pk] = (_snapshot_view(ordered[0]), _snapshot_view(ordered[1]))
        elif getattr(player, "previous_as_of", None) is not None:
            windows[player.pk] = (
                SimpleNamespace(
                    as_of=player.latest_snapshot_as_of,
                    team_id=player.latest_snapshot_team_id,
                    games_played=player.current_games_played,
                    season_id=player.latest_snapshot_season,
                ),
                SimpleNamespace(
                    as_of=player.previous_as_of,
                    team_id=player.previous_team_id,
                    games_played=player.previous_games_played,
                    season_id=player.latest_snapshot_season,
                ),
            )
    for player in players:
        player.latest_status = None
    if not windows:
        return players

    schedule = schedule_for_seasons({newer.season_id for newer, _ in windows.values()})
    prefetched_injuries = [
        report for player in players for report in getattr(player, "injury_history", None) or []
    ]
    if all(hasattr(player, "injury_history") for player in players):
        injuries = InjuryHistory(prefetched_injuries)
    else:
        injuries = injury_history(list(windows))
    for player in players:
        window = windows.get(player.pk)
        if window is not None:
            player.latest_status = status_between(*window, schedule, injuries, player.pk)
    return players
