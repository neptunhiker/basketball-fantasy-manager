"""A player's rest of the season: the games he can still play, and the points.

Games expected are his team's games left (82 less those played, as on the team
pages) less the ones he misses while injured -- his team's games before the
expected return date of an injury that keeps him out. Day-to-day, or out with
no return date, costs nothing: there is no date to count to.

The projection is this season's points per game times those games, times a
flat availability: hardly anyone plays every game that is left, and 85% is
about the league's rate. The same for everyone, so it never changes who ranks
above whom -- only how realistic the figure reads. A per-player rate can take
its place here without anything else changing.
"""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from django.db.models import (
    Case,
    Count,
    DecimalField,
    F,
    IntegerField,
    OuterRef,
    Q,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import Cast, Coalesce, Greatest, Round
from django.utils import timezone

from .models import DAY_TO_DAY_INJURY_STATUSES, GAME_LENGTH
from .team_schedule import NEXT_DAYS, season_games, team_schedules

EXPECTED_AVAILABILITY = Decimal("0.85")


def _out_until_return():
    """Players kept out by an injury with a return date: not day-to-day."""
    condition = Q(expected_return_date__isnull=False)
    for status in DAY_TO_DAY_INJURY_STATUSES:
        condition &= ~Q(latest_injury_status__iexact=status)
    return condition


def _per_team(figure, schedules):
    """`figure` of each player's team, as a CASE over the thirty team IDs."""
    return Case(
        *[When(team_id=team_id, then=Value(figure(entry))) for team_id, entry in schedules.items()],
        default=None,
        output_field=IntegerField(),
    )


def _missed_before_return(now, until=None):
    """His team's games still to come that fall before his return date."""
    games = (
        season_games()
        .filter(
            Q(home_team=OuterRef("team_id")) | Q(away_team=OuterRef("team_id")),
            tip_off__gt=now - GAME_LENGTH,
            tip_off__date__lt=OuterRef("expected_return_date"),
        )
        .order_by()
    )
    if until is not None:
        games = games.filter(tip_off__lt=until)
    return Subquery(
        games.values("season_year").annotate(n=Count("pk")).values("n")[:1],
        output_field=IntegerField(),
    )


def with_rest_of_season(queryset, now=None):
    """Annotate the rest of the season onto players, for lists to show and sort.

    Adds `team_games_left`, `games_missed`, `games_expected`, `next_days` and
    `projected_fp`, all NULL without a schedule, a team or (for the points)
    points per game. Needs `expected_return_date` and `latest_injury_status`
    from `views.with_expected_return` on the queryset first.
    """
    now = now or timezone.now()
    schedules = team_schedules(now=now)
    if not schedules:
        nothing = Value(None, output_field=IntegerField())
        return queryset.annotate(
            team_games_left=nothing,
            games_missed=nothing,
            games_expected=nothing,
            next_days=nothing,
            projected_fp=nothing,
        )
    out = _out_until_return()
    horizon = now + dt.timedelta(days=NEXT_DAYS)
    queryset = queryset.annotate(
        team_games_left=_per_team(lambda entry: entry.left, schedules),
        team_next_days=_per_team(lambda entry: entry.next_days, schedules),
        games_missed=Case(
            When(out, then=Coalesce(_missed_before_return(now), 0)),
            default=Value(0),
            output_field=IntegerField(),
        ),
        missed_next_days=Case(
            When(out, then=Coalesce(_missed_before_return(now, until=horizon), 0)),
            default=Value(0),
            output_field=IntegerField(),
        ),
    )
    # GREATEST skips NULLs in PostgreSQL, so a player without a team would
    # come out at 0 games rather than none; the CASE keeps him NULL.
    no_team = Q(team_games_left__isnull=True)
    queryset = queryset.annotate(
        games_expected=Case(
            When(no_team, then=Value(None)),
            default=Greatest(F("team_games_left") - F("games_missed"), Value(0)),
            output_field=IntegerField(),
        ),
        next_days=Case(
            When(no_team, then=Value(None)),
            default=Greatest(F("team_next_days") - F("missed_next_days"), Value(0)),
            output_field=IntegerField(),
        ),
    )
    return queryset.annotate(
        projected_fp=Cast(
            Round(
                F("current_fp_per_game")
                * F("games_expected")
                * Value(EXPECTED_AVAILABILITY, output_field=DecimalField()),
            ),
            IntegerField(),
        ),
    )


@dataclass
class RestOfSeason:
    """One player's rest of the season, for his page."""

    team_games_left: int
    games_missed: int
    next_days: int
    fp_per_game: Decimal | None
    return_date: dt.date | None
    team_schedule: object

    @property
    def games_expected(self):
        return max(self.team_games_left - self.games_missed, 0)

    @property
    def projected_fp(self):
        if self.fp_per_game is None:
            return None
        return round(self.fp_per_game * self.games_expected * EXPECTED_AVAILABILITY)

    @property
    def availability_percent(self):
        return round(EXPECTED_AVAILABILITY * 100)


def rest_of_season(player, now=None):
    """`player`'s RestOfSeason, or None without a team or a loaded schedule."""
    if player.team_id is None:
        return None
    now = now or timezone.now()
    games = list(season_games().for_team(player.team))
    schedule = team_schedules(now=now, games=games).get(player.team_id)
    if schedule is None:
        return None
    return_date = None
    missed = missed_next_days = 0
    if player.is_injured and player.expected_return:
        return_date = player.expected_return
        horizon = now + dt.timedelta(days=NEXT_DAYS)
        for game in games:
            on = timezone.localtime(game.tip_off).date()
            if game.tip_off > now - GAME_LENGTH and on < return_date:
                missed += 1
                if game.tip_off < horizon:
                    missed_next_days += 1
    return RestOfSeason(
        team_games_left=schedule.left,
        games_missed=missed,
        next_days=max(schedule.next_days - missed_next_days, 0),
        fp_per_game=player.current_fp_per_game,
        return_date=return_date,
        team_schedule=schedule,
    )
