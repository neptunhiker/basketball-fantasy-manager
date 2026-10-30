"""The Dream Team: the best roster anyone could have drafted, in hindsight.

Fifteen players, picked at the prices they had on draft day, that have scored
the most fantasy points since -- within the season's budget and the official
position minimums. It is a small integer linear program (a knapsack with
position constraints) and is solved exactly: a "best value first" pick cannot
be trusted here, because the budget and the minimums pull against each other.

`solve` is pure arithmetic over `Candidate`s, so it can be tested without a
database. `refresh` gathers the candidates for one import, solves, and stores
the result as a `DreamTeam`.
"""

import datetime as dt
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal

import numpy as np
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from django.utils.translation import gettext as _
from scipy.optimize import Bounds, LinearConstraint, milp

from apps.fantasy.models import MINIMUM_BY_POSITION, ROSTER_SIZE, PlayerSnapshot

from .models import DreamTeam, DreamTeamPick

# Salaries go to the solver in thousands of dollars: numbers of a few hundred to
# a few ten thousand sit well with its floating-point tolerances, where whole
# dollars in the tens of millions would not. `_check` re-checks the answer in
# exact decimals anyway.
SALARY_SCALE = Decimal("1000")


class SolverError(RuntimeError):
    """The solver stopped without proving an answer either way."""


class NoDreamTeam(Exception):
    """There is nothing to work a Dream Team out from (yet). The message says why."""


@dataclass(frozen=True)
class Candidate:
    """One player as the Dream Team sees him: draft-day price and position,
    and the points he has scored since."""

    player_id: object
    position: str
    salary: Decimal
    points: Decimal
    games_played: int = 0


@dataclass
class Changes:
    """How a Dream Team differs from the one the import before it left.

    `previous` is None for the first result of a season; the page then says
    "first calculation" instead of listing changes.
    """

    current: object
    previous: object
    came_in: list
    went_out: list
    stayed: int

    @property
    def is_first(self):
        return self.previous is None

    @property
    def unchanged(self):
        return self.previous is not None and not self.came_in and not self.went_out

    @property
    def salary_change(self):
        if self.previous is None:
            return None
        return self.current.total_salary - self.previous.total_salary


def previous_of(dream_team):
    """The stored result of the import before this one, in the same season.

    "Before" by stored results, not by imports: an import that produced no
    Dream Team is skipped, and the comparison reaches back to the last one that
    did. Picks, their players and teams come prefetched.
    """
    return (
        DreamTeam.objects.filter(season_id=dream_team.season_id, as_of__lt=dream_team.as_of)
        .order_by("-as_of")
        .prefetch_related("picks__player__team")
        .first()
    )


def changes(current, previous=None):
    """Who came into `current` and who went out, against the previous result.

    Worked out on read rather than stored: comparing two teams of fifteen is
    nothing, and a stored comparison would go stale the moment an older
    import is recalculated. Pass `previous` to skip looking it up.
    """
    if previous is None:
        previous = previous_of(current)
    now = {pick.player_id: pick for pick in current.picks.all()}
    if previous is None:
        return Changes(current=current, previous=None, came_in=[], went_out=[], stayed=len(now))
    before = {pick.player_id: pick for pick in previous.picks.all()}

    def by_points(picks):
        return sorted(picks, key=lambda pick: (-pick.total_fp, str(pick.player_id)))

    return Changes(
        current=current,
        previous=previous,
        came_in=by_points(pick for key, pick in now.items() if key not in before),
        went_out=by_points(pick for key, pick in before.items() if key not in now),
        stayed=len(now.keys() & before.keys()),
    )


def tip_off(season):
    """Midnight local time on the season's first day."""
    return timezone.make_aware(dt.datetime.combine(season.starts_on, dt.time.min))


def draft_moment(season):
    """When the game's prices count as draft-day prices.

    The moment signings opened, or, for a season without that date, tip-off.
    """
    return season.signings_open_at or tip_off(season)


def draft_snapshots(season):
    """The snapshots the draft-day prices come from.

    Those from before `draft_moment`, newest per player. When the first import
    of the season came only after signings had opened -- nobody scraped the
    prices the day the market opened -- the closest there is to draft day is
    that first import, as long as it was still before tip-off: the season's
    prices then, before a single game could move them. After tip-off there is
    nothing that stands for draft day, and the queryset is empty.
    """
    snapshots = PlayerSnapshot.objects.filter(season=season)
    before = snapshots.filter(as_of__lt=draft_moment(season))
    if before.exists():
        return before
    first = (
        snapshots.filter(as_of__lt=tip_off(season))
        .order_by("as_of")
        .values_list("as_of", flat=True)
        .first()
    )
    return snapshots.filter(as_of=first) if first else snapshots.none()


def candidates(season, as_of):
    """Every player who could have been drafted, with points at `as_of`.

    Returns `(draft_as_of, candidates)`: the moment of the draft-day snapshot
    the prices come from, and one `Candidate` per player in it. Two queries,
    one per snapshot moment. A player the source stopped listing after draft
    day keeps the points of his last snapshot before `as_of`.
    """
    draft = list(
        draft_snapshots(season)
        .latest_per_player()
        .only("player_id", "as_of", "salary", "position", "total_fp", "games_played")
    )
    if not draft:
        return None, []
    latest = {
        row.player_id: row
        for row in PlayerSnapshot.objects.filter(season=season, as_of__lte=as_of)
        .latest_per_player()
        .only("player_id", "total_fp", "games_played")
    }
    pool = []
    for row in draft:
        # Only missing when `as_of` lies before this player's draft-day row.
        now = latest.get(row.player_id, row)
        pool.append(
            Candidate(
                player_id=row.player_id,
                position=row.position,
                salary=row.salary,
                points=now.total_fp,
                games_played=now.games_played,
            )
        )
    return max(row.as_of for row in draft), pool


def missing_reason(season):
    """Why `season` has no stored Dream Team, as a key the page words.

    `no_stats`, `no_prices` (nothing that stands for draft day), `no_games`, or
    `not_calculated` when there would be something to work out -- which only
    happens before the first import since the Dream Team existed, or after a
    calculation failed.
    """
    snapshots = PlayerSnapshot.objects.filter(season=season)
    if not snapshots.exists():
        return "no_stats"
    prices_as_of = draft_snapshots(season).aggregate(moment=Max("as_of"))["moment"]
    if prices_as_of is None:
        return "no_prices"
    if not snapshots.filter(as_of__gt=prices_as_of, games_played__gt=0).exists():
        return "no_games"
    return "not_calculated"


def refresh(season, as_of=None):
    """Work out the Dream Team for one import and store it.

    `as_of` is the import's moment, the newest one of the season when left
    out. Overwrites a result already stored for that moment. Raises
    `NoDreamTeam` when there is nothing to work it out from -- and then also
    removes a result stored for that moment earlier, since whatever it was
    based on (a season date, the budget) no longer holds.
    """
    snapshots = PlayerSnapshot.objects.filter(season=season)
    if as_of is not None:
        snapshots = snapshots.filter(as_of__lte=as_of)
    # Pinned to a moment that has snapshots, so a result always names a real import.
    as_of = snapshots.aggregate(moment=Max("as_of"))["moment"]
    if as_of is None:
        raise NoDreamTeam(_("There are no stats for this season yet."))

    try:
        draft_as_of, pool = candidates(season, as_of)
        if not pool:
            raise NoDreamTeam(_("There are no prices from before the season started."))
        if as_of <= draft_as_of or not any(candidate.games_played for candidate in pool):
            raise NoDreamTeam(_("Nobody has played a game yet."))
        team = solve(pool, season.starting_cash)
        if team is None:
            raise NoDreamTeam(
                _("No team of %(size)d players fits the budget and the position minimums.")
                % {"size": ROSTER_SIZE}
            )
    except NoDreamTeam:
        DreamTeam.objects.filter(season=season, as_of=as_of).delete()
        raise

    with transaction.atomic():
        dream_team, _created = DreamTeam.objects.update_or_create(
            season=season,
            as_of=as_of,
            defaults={
                "draft_as_of": draft_as_of,
                "budget": season.starting_cash,
                "total_salary": sum((c.salary for c in team), Decimal(0)),
                "total_fp": sum((c.points for c in team), Decimal(0)),
                "candidates": len(pool),
                "pool": [
                    {
                        "player": str(c.player_id),
                        "position": c.position,
                        "salary": str(c.salary),
                        "points": str(c.points),
                        "games": c.games_played,
                    }
                    for c in pool
                    if c.games_played
                ],
            },
        )
        dream_team.picks.all().delete()
        DreamTeamPick.objects.bulk_create(
            DreamTeamPick(
                dream_team=dream_team,
                player_id=c.player_id,
                position=c.position,
                salary=c.salary,
                total_fp=c.points,
                games_played=c.games_played,
            )
            for c in team
        )
    return dream_team


def solve(candidates, budget, size=ROSTER_SIZE, minimums=MINIMUM_BY_POSITION):
    """The `size` candidates with the most points that fit `budget` and meet
    every position minimum.

    Returns them as a list, or None when no such team exists (too few players
    of a position, or nobody cheap enough). Among teams with equal points the
    answer is still always the same one for the same input, because the
    candidates are put in a fixed order before they reach the solver.
    """
    pool = sorted(candidates, key=lambda candidate: str(candidate.player_id))
    if len(pool) < size or sum(minimums.values()) > size:
        return None

    points = np.array([float(candidate.points) for candidate in pool])
    salaries = np.array([float(candidate.salary / SALARY_SCALE) for candidate in pool])

    rows = [np.ones(len(pool)), salaries]
    lower = [size, -np.inf]
    upper = [size, float(Decimal(budget) / SALARY_SCALE)]
    for position, minimum in minimums.items():
        rows.append(np.array([1.0 if c.position == position else 0.0 for c in pool]))
        lower.append(minimum)
        upper.append(np.inf)

    result = milp(
        # milp minimises, so the points go in negated.
        c=-points,
        constraints=LinearConstraint(np.vstack(rows), lower, upper),
        integrality=np.ones(len(pool)),
        bounds=Bounds(0, 1),
        # Zero gap: the proven best team, not one "within 0.01%" of it.
        options={"mip_rel_gap": 0},
    )
    if result.status == 2:  # infeasible
        return None
    if result.status != 0:
        raise SolverError(result.message)

    team = [candidate for candidate, x in zip(pool, result.x, strict=True) if x > 0.5]
    _check(team, budget, size, minimums)
    return team


def _check(team, budget, size, minimums):
    """Re-check the solver's answer in exact decimal arithmetic.

    The solver works in floating point with tolerances. Should it ever hand
    back a team a rounding error away from legal, this is where it stops,
    rather than on a page that claims the impossible.
    """
    counts = Counter(candidate.position for candidate in team)
    problems = []
    if len(team) != size:
        problems.append(f"{len(team)} players instead of {size}")
    if (total := sum((candidate.salary for candidate in team), Decimal(0))) > budget:
        problems.append(f"salary {total} over the budget of {budget}")
    for position, minimum in minimums.items():
        if counts[position] < minimum:
            problems.append(f"{counts[position]} {position} instead of at least {minimum}")
    if problems:
        raise SolverError("The solver returned an illegal team: " + "; ".join(problems))
