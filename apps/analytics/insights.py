"""The points race: every active roster's running total against the Dream Team --
and, beside it, what each of them is worth.

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

Each line also carries its team value per day (`Line.values_salary`): a
roster's from `apps.fantasy.value`, squad at that day's salaries plus cash;
the Dream Team's is that day's fifteen at that day's salaries plus the money
its draft left over, so the two compare like for like.

Reads only; works out nothing that is not already stored except the sums.
"""

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Prefetch
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.fantasy.models import PlayerSnapshot, Roster
from apps.fantasy.points import (
    ZERO,
    PointsHistory,
    cutoff,
    roster_points_by_moment,
    roster_spells,
    spell_players,
)
from apps.fantasy.value import roster_value_by_moment

from .models import DreamTeam, DreamTeamPick

DREAM_TEAM = "dream-team"
# How many rosters get a colour of their own. The palette (`.race-*` in
# assets/input.css) is the Dream Team's brand violet plus seven hues checked
# against it and each other for colour-blind readers; any roster past the
# seventh is drawn in a muted grey and told apart by its label instead.
COLORS = 7
MUTED = "muted"


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
    # Which `.race-*` colour draws it: "dt", "1".."7", or MUTED.
    color: str = MUTED
    # Team value per day, like `values` (None: no figure that day).
    values_salary: list = field(default_factory=list)

    @property
    def latest(self):
        return next((value for value in reversed(self.values) if value is not None), None)

    @property
    def last_gain(self):
        """What the newest day added, or None with fewer than two figures."""
        known = [value for value in self.values if value is not None]
        return known[-1] - known[-2] if len(known) >= 2 else None

    @property
    def latest_value(self):
        return next((value for value in reversed(self.values_salary) if value is not None), None)

    @property
    def value_change(self):
        """The newest team value against the first one: what it gained since the start."""
        known = [value for value in self.values_salary if value is not None]
        return known[-1] - known[0] if len(known) >= 2 else None


@dataclass
class Race:
    days: list  # local dates, oldest first; the first is the day signings closed
    lines: list = field(default_factory=list)  # the Dream Team first, then by points
    as_of: dt.datetime | None = None  # the newest import drawn
    budget: Decimal | None = None  # the season's starting budget, the value chart's reference

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
    results = _dream_team_results(season, start, moments[-1])
    # Points and value read the same spells and snapshots: loaded once, for
    # the rosters' players and the Dream Team's alike.
    spells = roster_spells(rosters, season, moments[-1])
    history = PointsHistory(
        season,
        spell_players(spells) | {pick.player_id for team in results for pick in team.picks.all()},
        until=moments[-1],
    )
    totals = roster_points_by_moment(rosters, season, moments, spells=spells, history=history)
    worth = roster_value_by_moment(rosters, season, moments, spells=spells, history=history)
    lines = [_dream_team_line(results, start, moments, history)]
    roster_lines = [
        Line(
            key=str(roster.pk),
            name=roster.name,
            manager=roster.manager.nick_name,
            icon=roster.icon,
            values=totals[roster.pk],
            values_salary=worth[roster.pk],
            is_own=viewer is not None and roster.manager.user_id == viewer.pk,
        )
        for roster in rosters
    ]
    _assign_colors(roster_lines, {str(roster.pk): roster.created_at for roster in rosters})
    roster_lines.sort(key=lambda line: (-(line.latest or ZERO), line.name.lower(), line.manager))
    lines += roster_lines
    return Race(days=days, lines=lines, as_of=moments[-1], budget=season.starting_cash)


def _assign_colors(lines, created):
    """A colour per roster that follows the roster, not its place in the race.

    The viewer's own rosters first, then the oldest: a roster keeps its colour
    when it overtakes another, and the one you care about is never the grey one.
    """
    ordered = sorted(lines, key=lambda line: (not line.is_own, created[line.key], line.key))
    for index, line in enumerate(ordered):
        line.color = str(index + 1) if index < COLORS else MUTED


def _dream_team_results(season, start, until):
    """The stored Dream Teams between the start and `until`, oldest first,
    each with its picks' players. Two queries."""
    return list(
        DreamTeam.objects.filter(season=season, as_of__gt=start, as_of__lte=until)
        .order_by("as_of")
        .only("as_of", "total_fp", "budget", "total_salary")
        .prefetch_related(
            Prefetch("picks", queryset=DreamTeamPick.objects.only("dream_team_id", "player_id"))
        )
    )


def _dream_team_value(team, moment, history):
    """`team`'s fifteen at their salaries at `moment`, plus what its draft left over."""
    squad = sum(
        (history.salary_at(pick.player_id, moment) or ZERO for pick in team.picks.all()), ZERO
    )
    return squad + team.budget - team.total_salary


def _dream_team_line(results, start, moments, history):
    """The Dream Team's total and team value at each moment, from the stored results.

    A day takes its last stored result. A day whose import produced none --
    not worked out yet, or nobody could be bought -- has no figure and the
    line has a gap there, rather than a made-up zero. The start's points are
    zero; its value is the first result's fifteen at the start's prices, the
    closest there is to what it would have been worth then.
    """
    by_day = {timezone.localdate(team.as_of): team for team in results}
    values, worth = [], []
    for moment in moments:
        if moment == start:
            values.append(ZERO)
            worth.append(_dream_team_value(results[0], moment, history) if results else None)
            continue
        team = by_day.get(timezone.localdate(moment))
        values.append(Decimal(team.total_fp) if team else None)
        worth.append(_dream_team_value(team, moment, history) if team else None)
    return Line(
        key=DREAM_TEAM,
        name=_("Dream Team"),
        values=values,
        values_salary=worth,
        is_dream_team=True,
        color="dt",
    )
