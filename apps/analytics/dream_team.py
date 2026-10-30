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


def draft_moment(season):
    """When the game's prices count as draft-day prices.

    The moment signings opened, or, for a season without that date, tip-off at
    midnight local time on its first day.
    """
    if season.signings_open_at:
        return season.signings_open_at
    return timezone.make_aware(dt.datetime.combine(season.starts_on, dt.time.min))


def candidates(season, as_of):
    """Every player who could have been drafted, with points at `as_of`.

    Returns `(draft_as_of, candidates)`: the moment of the draft-day snapshot
    the prices come from, and one `Candidate` per player in it. Two queries,
    one per snapshot moment. A player the source stopped listing after draft
    day keeps the points of his last snapshot before `as_of`.
    """
    draft = list(
        PlayerSnapshot.objects.filter(season=season, as_of__lt=draft_moment(season))
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
