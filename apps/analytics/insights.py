"""The points race: every active roster's running total against the Dream Team.

One figure per day for each line, from the day signings closed -- where every
line starts at zero -- to the newest import. A day's figure is the last
import of that day; days without an import are not on the axis at all, so a
quiet day neither flattens a line nor shows up as a gap.

The rosters' figures come from `apps.fantasy.points`, the same arithmetic as
the roster timeline. The Dream Team's are the stored results
(`apps.analytics.dream_team.refresh`): the best roster that could have been
bought on draft day, as it stood after each import. It is a different fifteen
from one day to the next, and it never trades -- so it can be beaten by a
manager who trades well, which is rather the point of drawing it.

Reads only; works out nothing that is not already stored except the sums.
"""

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from django.utils import timezone
from django.utils.translation import gettext as _

from apps.fantasy.models import PlayerSnapshot, Roster
from apps.fantasy.points import ZERO, cutoff, roster_points_by_moment

from .models import DreamTeam

DREAM_TEAM = "dream-team"


@dataclass
class Line:
    """One line of the chart and one row of the standings under it."""

    key: str
    name: str
    values: list  # one Decimal (or None: no figure that day) per day
    manager: str = ""
    icon: str = ""
    is_dream_team: bool = False
    is_own: bool = False

    @property
    def latest(self):
        return next((value for value in reversed(self.values) if value is not None), None)

    @property
    def last_gain(self):
        """What the newest day added, or None with fewer than two figures."""
        known = [value for value in self.values if value is not None]
        return known[-1] - known[-2] if len(known) >= 2 else None


@dataclass
class Race:
    days: list  # local dates, oldest first; the first is the day signings closed
    lines: list = field(default_factory=list)  # the Dream Team first, then by points
    as_of: dt.datetime | None = None  # the newest import drawn

    @property
    def dream_team(self):
        return next((line for line in self.lines if line.is_dream_team), None)

    @property
    def rosters(self):
        return [line for line in self.lines if not line.is_dream_team]

    @property
    def standings(self):
        """Every line by points so far, best first, with its gap to the Dream Team.

        A row is `(rank, line, gap)`; `gap` is how many points the line is
        behind the Dream Team (negative when ahead), None for the Dream Team
        itself or when it has no figure.
        """
        best = self.dream_team.latest if self.dream_team else None
        ordered = sorted(
            self.lines,
            key=lambda line: (-(line.latest or ZERO), not line.is_dream_team, line.name.lower()),
        )
        return [
            (
                rank,
                line,
                None if line.is_dream_team or best is None else best - (line.latest or ZERO),
            )
            for rank, line in enumerate(ordered, start=1)
        ]


def race_moments(season):
    """The moments the race is drawn at: the cutoff, then each day's last import.

    Returns `(start, [moment, ...])`. The start is where every line is zero;
    an import on the same day as the cutoff takes the start's place on the
    axis, since a day can only be drawn once.
    """
    start = cutoff(season)
    imports = (
        PlayerSnapshot.objects.filter(season=season, as_of__gt=start)
        .order_by("as_of")
        .values_list("as_of", flat=True)
        .distinct()
    )
    last_per_day = {}
    for moment in imports:
        last_per_day[timezone.localdate(moment)] = moment
    return start, list(last_per_day.values())


def points_race(season, viewer=None):
    """The race for `season`, or None before there is anything to draw.

    Nothing to draw: no import since signings closed. Without active rosters
    the race still has its Dream Team line; the page says why it is alone.
    """
    start, moments = race_moments(season)
    if not moments:
        return None

    days = [timezone.localdate(moment) for moment in moments]
    if timezone.localdate(start) != days[0]:
        moments = [start, *moments]
        days = [timezone.localdate(start), *days]

    rosters = list(Roster.objects.filter(season=season, is_active=True).select_related("manager"))
    totals = roster_points_by_moment(rosters, season, moments)
    lines = [_dream_team_line(season, start, moments)]
    roster_lines = [
        Line(
            key=str(roster.pk),
            name=roster.name,
            manager=roster.manager.nick_name,
            icon=roster.icon,
            values=totals[roster.pk],
            is_own=viewer is not None and roster.manager.user_id == viewer.pk,
        )
        for roster in rosters
    ]
    roster_lines.sort(key=lambda line: (-(line.latest or ZERO), line.name.lower(), line.manager))
    lines += roster_lines
    return Race(days=days, lines=lines, as_of=moments[-1])


def _dream_team_line(season, start, moments):
    """The Dream Team's total at each moment, from the stored results.

    A day takes its last stored result. A day whose import produced none --
    not worked out yet, or nobody could be bought -- has no figure and the
    line has a gap there, rather than a made-up zero. The start is zero.
    """
    by_day = {}
    for as_of, total in (
        DreamTeam.objects.filter(season=season, as_of__gt=start, as_of__lte=moments[-1])
        .order_by("as_of")
        .values_list("as_of", "total_fp")
    ):
        by_day[timezone.localdate(as_of)] = Decimal(total)
    values = [
        ZERO if moment == start else by_day.get(timezone.localdate(moment)) for moment in moments
    ]
    return Line(key=DREAM_TEAM, name=_("Dream Team"), values=values, is_dream_team=True)
