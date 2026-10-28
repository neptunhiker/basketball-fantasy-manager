"""The NBA schedule: load the season's games, so played and left can be counted.

The feed gives 31 days a request, so a season is six or seven of them out of
the 90 a month the injury button uses too. It is therefore loaded whole a few
times a season -- at the start, after the NBA Cup's group stage adds two games
a team, after a postponement -- and nothing in between: whether a game has
been played is read off the clock, not off a result.

Every load is stored as a whole: a game already known is updated in place by
its ESPN ID (a moved game keeps its ID and gets the new date), and a game still
to come that the feed no longer lists is dropped. A game in the past is never
dropped, whatever a later load says.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.fantasy.models import Season

from . import quota
from .models import Game, NbaApiUsage, Team
from .services import (
    FEED_TEAM_CODES,
    PROVIDER,
    DailyApiLimitExceeded,
    NbaApiError,
    check_monthly_reserve,
)

logger = logging.getLogger(__name__)

# The daily tally's name: apart from the injury button's, so a load never
# uses up the one injury update a day. The monthly reserve is shared.
TALLY = "api-basketball-nba-schedule"
# The longest stretch the feed gives in one request.
RANGE_DAYS = 31
# ESPN's season type for the regular season (1 preseason, 3 playoffs).
REGULAR_SEASON_TYPE = 2


class ScheduleDisabled(NbaApiError):
    """NBA_SCHEDULE_ENABLED is off, so no request may be made."""


@dataclass(frozen=True)
class ParsedGame:
    espn_id: str
    tip_off: datetime
    home_espn_id: int | None
    home_abbreviation: str
    away_espn_id: int | None
    away_abbreviation: str
    status: str
    note: str
    is_regular_season: bool


@dataclass(frozen=True)
class ScheduleLoadResult:
    games: int
    created: int
    updated: int
    removed: int
    unmatched: int

    def __str__(self):
        return (
            f"{self.games} games, {self.created} new, {self.updated} already known, "
            f"{self.removed} removed, {self.unmatched} with an unknown team."
        )


# -- which season, and whether to load it ----------------------------------------


def current_season():
    return Season.objects.filter(is_current=True).first()


def season_span(season):
    """The days to ask for: the season's dates, with a day's slack either side.

    The feed's dates are American: a game on the season's first evening in New
    York is the next day in UTC.
    """
    return season.starts_on - timedelta(days=1), season.ends_on + timedelta(days=1)


def ranges(first, last):
    """(from, to) pairs of at most RANGE_DAYS days, both ends included."""
    start = first
    while start <= last:
        end = min(start + timedelta(days=RANGE_DAYS - 1), last)
        yield start, end
        start = end + timedelta(days=1)


def why_not_now(force=False, today=None):
    """Why no load should be made right now, or None when one may.

    The schedule skips for any of these; the import command refuses. `force`
    sets aside a season that is over and the monthly reserve, never the switch,
    the key or a missing season -- nor the daily limit, kept by the fetch.
    """
    if not settings.NBA_SCHEDULE_ENABLED:
        return "NBA_SCHEDULE_ENABLED is off"
    if not settings.RAPID_API_KEY:
        return "RAPID_API_KEY is not set"
    season = current_season()
    if season is None:
        return "no current season is set up"
    if force:
        return None
    if season.ends_on < (today or timezone.localdate()):
        return f"the current season {season.label} is over"
    low = quota.low(PROVIDER, settings.RAPID_API_MONTHLY_RESERVE)
    if low is not None:
        return quota.describe(low, settings.RAPID_API_MONTHLY_RESERVE)
    return None


# -- the request -----------------------------------------------------------------


def fetch_range(first, last):
    """The games from `first` to `last`, one request, consuming one of today's."""
    if not settings.NBA_SCHEDULE_ENABLED:
        raise ScheduleDisabled("The NBA schedule is switched off (NBA_SCHEDULE_ENABLED).")
    if not settings.RAPID_API_KEY:
        raise NbaApiError("RAPID_API_KEY is not configured.")

    check_monthly_reserve()
    _reserve_api_call()
    query = urlencode({"from": first.isoformat(), "to": last.isoformat()})
    request = Request(
        f"{settings.NBA_SCHEDULE_URL}?{query}",
        headers={
            "X-RapidAPI-Key": settings.RAPID_API_KEY,
            "X-RapidAPI-Host": settings.RAPID_API_HOST,
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=settings.RAPID_API_TIMEOUT) as response:
            quota.record(PROVIDER, getattr(response, "headers", None))
            if response.status != 200:
                raise NbaApiError(f"NBA schedule returned HTTP {response.status}.")
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        quota.record(PROVIDER, exc.headers)
        raise NbaApiError(f"NBA schedule returned HTTP {exc.code}.") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise NbaApiError("NBA schedule request failed or returned invalid JSON.") from exc

    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise NbaApiError("NBA schedule returned an unexpected response.")
    return payload


@transaction.atomic
def _reserve_api_call(now=None):
    """Count one request against today's schedule tally, or refuse it."""
    now = now or timezone.now()
    usage, _ = NbaApiUsage.objects.get_or_create(
        provider=TALLY,
        usage_date=timezone.localtime(now).date(),
        defaults={"request_count": 0},
    )
    usage = NbaApiUsage.objects.select_for_update().get(pk=usage.pk)
    if usage.request_count >= settings.NBA_SCHEDULE_DAILY_LIMIT:
        raise DailyApiLimitExceeded(
            f"The NBA schedule's daily limit of {settings.NBA_SCHEDULE_DAILY_LIMIT} "
            "requests has been reached."
        )
    usage.request_count += 1
    usage.save(update_fields=["request_count", "updated_at"])


# -- reading the feed ------------------------------------------------------------


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _tip_off(value):
    """The feed's "2026-10-21T23:00Z" as an aware datetime, or None."""
    try:
        moment = datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if timezone.is_aware(moment) else None


def _side(game, side):
    team = game.get(side)
    if not isinstance(team, dict):
        return None, ""
    return _int(team.get("id")), _text(team.get("abbreviation")).upper()


def parse_game(game):
    """A ParsedGame for one feed entry, or None when it cannot be placed."""
    if not isinstance(game, dict):
        return None
    espn_id = _text(game.get("id"))
    tip_off = _tip_off(game.get("date"))
    home_id, home_code = _side(game, "home")
    away_id, away_code = _side(game, "away")
    if not espn_id or tip_off is None or not (home_id or home_code) or not (away_id or away_code):
        return None
    season = game.get("season") if isinstance(game.get("season"), dict) else {}
    status = game.get("status") if isinstance(game.get("status"), dict) else {}
    notes = [_text(note) for note in game.get("notes") or [] if _text(note)]
    return ParsedGame(
        espn_id=espn_id,
        tip_off=tip_off,
        home_espn_id=home_id,
        home_abbreviation=home_code,
        away_espn_id=away_id,
        away_abbreviation=away_code,
        status=_text(status.get("name"))[:40],
        note=" · ".join(notes)[:200],
        is_regular_season=_int(season.get("type")) == REGULAR_SEASON_TYPE,
    )


def parse_games(payload):
    games = []
    for entry in payload.get("data") or []:
        parsed = parse_game(entry)
        if parsed is None:
            logger.warning("NBA schedule: skipped an entry it could not read: %r", entry)
            continue
        games.append(parsed)
    return games


class TeamFinder:
    """Our team for a side of a game: by ESPN ID, else by ESPN's abbreviation."""

    def __init__(self):
        teams = list(Team.objects.all())
        self.by_espn_id = {team.espn_id: team for team in teams if team.espn_id}
        self.by_code = {team.abbreviation: team for team in teams}

    def find(self, espn_id, code):
        team = self.by_espn_id.get(espn_id)
        if team is None and code:
            team = self.by_code.get(FEED_TEAM_CODES.get(code, code))
        return team


# -- storing a load --------------------------------------------------------------


def load_season(season=None, payloads=None, now=None):
    """Load the whole season and store it; fetch it first when no payloads are given.

    Every request is made before anything is written, so a load that fails
    half-way changes nothing -- and drops no game on the strength of half a
    season.
    """
    season = season or current_season()
    if season is None:
        raise NbaApiError("No current season is set up.")
    if payloads is None:
        payloads = [fetch_range(first, last) for first, last in ranges(*season_span(season))]
    games = {}
    for payload in payloads:
        for game in parse_games(payload):
            games[game.espn_id] = game
    return _store(season.ends_on.year, list(games.values()), now or timezone.now())


@transaction.atomic
def _store(season_year, games, now):
    finder = TeamFinder()
    known = {
        game.espn_id: game for game in Game.objects.filter(espn_id__in=[g.espn_id for g in games])
    }

    created = updated = unmatched = 0
    seen = set()
    for game in games:
        home = finder.find(game.home_espn_id, game.home_abbreviation)
        away = finder.find(game.away_espn_id, game.away_abbreviation)
        if home is None or away is None:
            unmatched += 1
            logger.warning(
                "NBA schedule: unknown team in %s @ %s (game %s).",
                game.away_abbreviation,
                game.home_abbreviation,
                game.espn_id,
            )
            continue
        seen.add(game.espn_id)
        fields = {
            "season_year": season_year,
            "tip_off": game.tip_off,
            "home_team": home,
            "away_team": away,
            "status": game.status,
            "note": game.note,
            "is_regular_season": game.is_regular_season,
        }
        stored = known.get(game.espn_id)
        if stored is None:
            Game.objects.create(espn_id=game.espn_id, **fields)
            created += 1
        else:
            for name, value in fields.items():
                setattr(stored, name, value)
            stored.save()
            updated += 1

    # A game still to come that this load no longer lists has been dropped
    # from the schedule; one in the past stays, whatever the feed says now.
    removed, _ = (
        Game.objects.filter(season_year=season_year, tip_off__gt=now)
        .exclude(espn_id__in=seen)
        .delete()
    )
    return ScheduleLoadResult(
        games=len(games), created=created, updated=updated, removed=removed, unmatched=unmatched
    )
