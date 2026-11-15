"""What a roster's players scored for it: the one place that adds it up.

A player counts for a roster only while he is on it. What he scored in that
spell is his season total at the end of it minus his total at its start, both
read off the daily snapshots -- so it is as precise as they are, which is why
the screens call it approximate.

The roster timeline (one number per spell) and Analytics -> Insights (a running
total per roster, day by day) both ask this module, so the two can never tell
a manager different things about the same roster.

A read model: nothing here writes.
"""

import bisect
from decimal import Decimal

from .models import PlayerSnapshot, RosterPlayer

ZERO = Decimal("0")


def cutoff(season):
    """The moment a roster starts counting: when signings closed.

    Everything bought and sold before it is the manager trying things, not
    players who ever scored for the roster. A season without a cutoff keeps
    signings open all season, so there is no pre-season to leave out and its
    first day stands in.
    """
    return season.signings_close_at or season.season_open_at


class PointsHistory:
    """Each player's season total over time, from the snapshots of one season.

    One query for any number of players. `total_at` then answers from memory.
    """

    def __init__(self, season, player_ids, until=None):
        self.season = season
        self._series = {}
        snapshots = PlayerSnapshot.objects.filter(season=season, player_id__in=set(player_ids))
        if until is not None:
            snapshots = snapshots.filter(as_of__lte=until)
        for player_id, as_of, total in snapshots.order_by("player_id", "as_of").values_list(
            "player_id", "as_of", "total_fp"
        ):
            moments, totals = self._series.setdefault(player_id, ([], []))
            moments.append(as_of)
            totals.append(total)

    def __contains__(self, player_id):
        return player_id in self._series

    def total_at(self, player_id, moment):
        """His season total as the last snapshot at or before `moment` had it.

        None when nothing had been recorded for him yet.
        """
        moments, totals = self._series.get(player_id, ((), ()))
        index = bisect.bisect_right(moments, moment) - 1
        return totals[index] if index >= 0 else None

    def spell_points(self, player_id, start, end):
        """What he scored between `start` and `end`, or None when unknown.

        Unknown when there is no snapshot of him at all, or none from before a
        mid-season arrival: his first total would then include games he played
        for nobody, and no number is better than a wrong one. Before the
        season's first game there is nothing to include, so a missing total
        there is simply zero. Never negative: a corrected import must not make
        a player cost a roster points.
        """
        if player_id not in self:
            return None
        before = self.total_at(player_id, start)
        if before is None:
            if start > self.season.season_open_at:
                return None
            before = ZERO
        after = self.total_at(player_id, end)
        if after is None:
            return None
        return max(ZERO, after - before)


def roster_points_by_moment(rosters, season, moments):
    """Each roster's points so far at each of `moments` (oldest first).

    Returns `{roster.pk: [Decimal, ...]}`, one figure per moment: the sum of
    what every player scored while on the roster, from `cutoff(season)` up to
    that moment. A spell whose points are unknown (see `spell_points`) adds
    nothing, as on the roster timeline. Moments before the cutoff are zero.

    Two queries for any number of rosters: their spells and the snapshots.
    """
    moments = list(moments)
    rosters = list(rosters)
    if not moments or not rosters:
        return {roster.pk: [ZERO] * len(moments) for roster in rosters}

    start_moment = cutoff(season)
    until = moments[-1]
    spells_by_roster = {roster.pk: [] for roster in rosters}
    for spell in RosterPlayer.objects.filter(roster__in=rosters, added_at__lte=until).only(
        "roster_id", "player_id", "added_at", "removed_at"
    ):
        if spell.removed_at is None or spell.removed_at > start_moment:
            spells_by_roster[spell.roster_id].append(spell)

    history = PointsHistory(
        season,
        {spell.player_id for spells in spells_by_roster.values() for spell in spells},
        until=until,
    )

    result = {}
    for roster in rosters:
        spans = [
            (spell.player_id, max(spell.added_at, start_moment), spell.removed_at)
            for spell in spells_by_roster[roster.pk]
        ]
        series = []
        for moment in moments:
            total = ZERO
            if moment > start_moment:
                for player_id, start, end in spans:
                    if start >= moment:
                        continue
                    points = history.spell_points(
                        player_id, start, moment if end is None or end > moment else end
                    )
                    if points is not None:
                        total += points
            series.append(total)
        result[roster.pk] = series
    return result
