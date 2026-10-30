"""The Dream Team: the best roster anyone could have drafted, in hindsight.

Fifteen players, picked at the prices they had on draft day, that have scored
the most fantasy points since -- within the season's budget and the official
position minimums. It is a small integer linear program (a knapsack with
position constraints) and is solved exactly: a "best value first" pick cannot
be trusted here, because the budget and the minimums pull against each other.

`solve` is pure arithmetic over `Candidate`s, so it can be tested without a
database.
"""

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

from apps.fantasy.models import MINIMUM_BY_POSITION, ROSTER_SIZE

# Salaries go to the solver in thousands of dollars: numbers of a few hundred to
# a few ten thousand sit well with its floating-point tolerances, where whole
# dollars in the tens of millions would not. `_check` re-checks the answer in
# exact decimals anyway.
SALARY_SCALE = Decimal("1000")


class SolverError(RuntimeError):
    """The solver stopped without proving an answer either way."""


@dataclass(frozen=True)
class Candidate:
    """One player as the Dream Team sees him: draft-day price and position,
    and the points he has scored since."""

    player_id: object
    position: str
    salary: Decimal
    points: Decimal


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
