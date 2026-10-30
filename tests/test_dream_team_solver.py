"""The Dream Team's optimiser, checked against brute force on small pools.

No database: `solve` only sees `Candidate`s.
"""

import itertools
import random
from collections import Counter
from decimal import Decimal

import pytest

from apps.analytics.dream_team import Candidate, solve

# Small enough rules that every combination can be tried.
SIZE = 5
MINIMUMS = {"G": 2, "F": 1, "C": 1}


def _candidate(name, position, salary_m, points):
    return Candidate(
        player_id=name,
        position=position,
        salary=Decimal(str(salary_m)) * 1_000_000,
        points=Decimal(str(points)),
    )


def _total(team, field):
    return sum((getattr(c, field) for c in team), Decimal(0))


def _legal(team, budget, size=SIZE, minimums=MINIMUMS):
    counts = Counter(c.position for c in team)
    return (
        len(team) == size
        and _total(team, "salary") <= budget
        and all(counts[p] >= n for p, n in minimums.items())
    )


def _brute_force_best(pool, budget):
    """The highest points total of any legal team, or None."""
    totals = [
        _total(team, "points")
        for team in itertools.combinations(pool, SIZE)
        if _legal(team, budget)
    ]
    return max(totals, default=None)


def _random_pool(rng, n):
    return [
        _candidate(
            f"p{i:02d}",
            rng.choice("GGFFC"),
            # Salaries in $10k steps, like the game's.
            Decimal(rng.randint(50, 1500)) / 100,
            # Halves, like real fantasy points, and the odd negative score.
            Decimal(rng.randint(-10, 300)) / 2,
        )
        for i in range(n)
    ]


@pytest.mark.parametrize("seed", range(60))
def test_matches_brute_force_on_random_pools(seed):
    rng = random.Random(seed)
    pool = _random_pool(rng, rng.randint(SIZE, 16))
    budget = Decimal(rng.randint(5, 45)) * 1_000_000

    team = solve(pool, budget, size=SIZE, minimums=MINIMUMS)
    best = _brute_force_best(pool, budget)

    if best is None:
        assert team is None
    else:
        assert team is not None
        assert _legal(team, budget)
        assert _total(team, "points") == best


def test_beats_picking_the_best_value_first():
    """Points per dollar fills the budget with cheap players and then cannot
    afford the one star that matters. Here the cheap Center is the best value
    of all, but the right team pays for the expensive one."""
    pool = [
        _candidate("g1", "G", 2, 40),
        _candidate("g2", "G", 2, 40),
        _candidate("f1", "F", 2, 40),
        _candidate("f2", "F", 1, 30),
        _candidate("c_cheap", "C", 0.5, 20),  # 40 points per $M: best value
        _candidate("c_star", "C", 4, 100),  # 25 points per $M
    ]
    team = solve(pool, Decimal("11000000"), size=SIZE, minimums=MINIMUMS)

    assert {c.player_id for c in team} == {"g1", "g2", "f1", "f2", "c_star"}


def test_spends_up_to_exactly_the_budget():
    pool = [
        _candidate("g1", "G", 3, 10),
        _candidate("g2", "G", 3, 10),
        _candidate("f1", "F", 3, 10),
        _candidate("c1", "C", 3, 10),
        _candidate("x_exact", "F", 3, 50),
        _candidate("x_cheap", "F", 1, 5),
    ]
    team = solve(pool, Decimal("15000000"), size=SIZE, minimums=MINIMUMS)

    assert "x_exact" in {c.player_id for c in team}
    assert _total(team, "salary") == Decimal("15000000")


def test_ten_cents_over_the_budget_is_over_the_budget():
    """Every player costs $3M, so any team with `x_over` comes to
    $15,000,000.10 -- which the floating-point solver must not round away."""
    pool = [
        _candidate("g1", "G", 3, 10),
        _candidate("g2", "G", 3, 10),
        _candidate("f1", "F", 3, 10),
        _candidate("c1", "C", 3, 10),
        _candidate("x_over", "F", "3.0000001", 50),  # 3,000,000.10
        _candidate("x_fits", "F", 3, 5),
    ]
    team = solve(pool, Decimal("15000000"), size=SIZE, minimums=MINIMUMS)

    assert {c.player_id for c in team} == {"g1", "g2", "f1", "c1", "x_fits"}


def test_fills_the_free_places_with_any_position():
    pool = [
        _candidate("g1", "G", 1, 10),
        _candidate("g2", "G", 1, 10),
        _candidate("f1", "F", 1, 10),
        _candidate("c1", "C", 1, 10),
        _candidate("c2", "C", 1, 90),
        _candidate("f2", "F", 1, 5),
    ]
    team = solve(pool, Decimal("5000000"), size=SIZE, minimums=MINIMUMS)

    assert Counter(c.position for c in team) == {"G": 2, "F": 1, "C": 2}


def test_no_team_without_enough_of_a_position():
    pool = [_candidate(f"g{i}", "G", 1, 10) for i in range(6)] + [_candidate("f1", "F", 1, 10)]
    assert solve(pool, Decimal("100000000"), size=SIZE, minimums=MINIMUMS) is None


def test_no_team_when_nobody_fits_the_budget():
    pool = _random_pool(random.Random(1), 10)
    assert solve(pool, Decimal("100"), size=SIZE, minimums=MINIMUMS) is None


def test_no_team_from_too_few_players():
    pool = _random_pool(random.Random(2), SIZE - 1)
    assert solve(pool, Decimal("100000000"), size=SIZE, minimums=MINIMUMS) is None


def test_the_same_team_for_the_same_input_whatever_the_order():
    """Ties are common early in a season (lots of 0-point players at the
    minimum salary). The answer must not depend on the order of the input."""
    pool = [_candidate(f"p{i}", "GFC"[i % 3], 1, 10) for i in range(12)]
    first = solve(pool, Decimal("5000000"), size=SIZE, minimums=MINIMUMS)
    shuffled = pool[:]
    random.Random(3).shuffle(shuffled)
    second = solve(shuffled, Decimal("5000000"), size=SIZE, minimums=MINIMUMS)

    assert [c.player_id for c in first] == [c.player_id for c in second]


def test_the_real_rules_on_a_full_sized_pool():
    """15 players, 5/5/2, $63M, on a pool the size of a real season. Only
    checks legality and that it finishes: brute force is out of reach here."""
    rng = random.Random(4)
    pool = _random_pool(rng, 500)
    team = solve(pool, Decimal("63000000"))

    assert _legal(team, Decimal("63000000"), size=15, minimums={"G": 5, "F": 5, "C": 2})
