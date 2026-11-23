"""What a roster was worth: its team value at any past moment.

Team value is the roster screen's figure (`Roster.team_value`) and the
official game's salary cap: the squad at today's prices plus the cash on
hand. Over time it is the same sum at each moment:

- **Squad**: every player on the roster then, at his salary as the last
  snapshot at or before that moment had it (`PointsHistory.salary_at`).
- **Cash**: anchored on today rather than replayed from the starting budget:
  cash then = cash now - every cash flow since. So the newest figure is
  exactly the roster screen's, and a ledger that does not add up to the
  balance (see `apps.fantasy.ledger`) can only show in the past.

Analytics -> Insights draws it; a read model, nothing here writes.
"""

import bisect
from decimal import Decimal

from apps.nba.models import Player

from .models import Transaction
from .points import ZERO, PointsHistory, roster_spells, spell_players


def roster_value_by_moment(rosters, season, moments, *, spells=None, history=None):
    """Each roster's team value at each of `moments` (oldest first).

    Returns `{roster.pk: [Decimal, ...]}`, one figure per moment. Meant for
    moments from the cutoff on (`points.cutoff`): the spells are those that
    count from there, as in `roster_points_by_moment`, and `spells` and
    `history` can be shared with it.

    A player is always priced: before his first snapshot his first one
    stands in, and without any snapshot his current salary -- a hole in one
    player's history must not sink a whole roster's line.

    Two queries for any number of rosters, plus the two `spells` and
    `history` save when passed (and one more only if a player has no
    snapshot at all).
    """
    moments = list(moments)
    rosters = list(rosters)
    if not moments or not rosters:
        return {roster.pk: [ZERO] * len(moments) for roster in rosters}

    until = moments[-1]
    spells_by_roster = spells if spells is not None else roster_spells(rosters, season, until)
    if history is None:
        history = PointsHistory(season, spell_players(spells_by_roster), until=until)
    unpriced = {
        player_id for player_id in spell_players(spells_by_roster) if player_id not in history
    }
    fallback = (
        dict(Player.objects.filter(pk__in=unpriced).values_list("pk", "current_salary").iterator())
        if unpriced
        else {}
    )

    flows = _cash_flows_after(rosters, moments[0])
    result = {}
    for roster in rosters:
        times, running = flows.get(roster.pk, ((), ()))
        series = []
        for moment in moments:
            squad = ZERO
            for spell in spells_by_roster[roster.pk]:
                if spell.added_at > moment or (
                    spell.removed_at is not None and spell.removed_at <= moment
                ):
                    continue
                salary = history.salary_at(spell.player_id, moment)
                if salary is None:
                    salary = fallback.get(spell.player_id) or ZERO
                squad += salary
            series.append(squad + roster.cash - _flow_after(times, running, moment))
        result[roster.pk] = series
    return result


def _cash_flows_after(rosters, since):
    """Each roster's moves after `since`, as `(times, running totals)`.

    `running[i]` is the sum of the cash flows of moves `i` onwards, so the
    money that moved after any moment is one binary search away.
    """
    rows = {}
    for roster_id, occurred_at, delta in (
        Transaction.objects.filter(roster__in=rosters, occurred_at__gt=since)
        .order_by("roster_id", "occurred_at")
        .values_list("roster_id", "occurred_at", "cash_delta")
    ):
        times, deltas = rows.setdefault(roster_id, ([], []))
        times.append(occurred_at)
        deltas.append(delta)
    flows = {}
    for roster_id, (times, deltas) in rows.items():
        running = [ZERO] * len(deltas)
        total = Decimal(0)
        for index in range(len(deltas) - 1, -1, -1):
            total += deltas[index]
            running[index] = total
        flows[roster_id] = (times, running)
    return flows


def _flow_after(times, running, moment):
    """The cash that moved after `moment`: a move at `moment` itself has happened."""
    index = bisect.bisect_right(times, moment)
    return running[index] if index < len(running) else ZERO
