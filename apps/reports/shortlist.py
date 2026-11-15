"""The legal trades a roster could make right now, best first.

What the AI chooses from: it may only recommend a trade on this list, so a
recommendation can never break the game's rules. A trade swaps one squad
player for one market player at today's salaries; it is legal when the cash
covers it and it breaks no position minimum the roster meets today (the same
rule as the trade screen's `trade_preview`).

Ranked by projected rest-of-season points gained, with the predicted salary
gain at the next update as the tiebreak: points decide the standings, cash only
pays for later trades. Pure arithmetic over `Candidate`s, no database.
"""

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal

from apps.fantasy.models import position_shortfalls
from apps.nba.models import Player

# At most this many trades sending out (or bringing in) the same player, and in
# all: twenty-five ways of buying one bargain is one idea, not twenty-five.
PER_PLAYER = 4
LIMIT = 25


@dataclass(frozen=True)
class Candidate:
    """A player as the shortlist sees him: price, position and prospects."""

    player_id: object
    ref: str
    name: str
    position: str
    salary: Decimal
    # Rest-of-season points (`rest_of_season.with_rest_of_season`), None unknown.
    projected_fp: int | None
    # Predicted salary change at the next update (`salary_outlook`), None unknown.
    salary_change: Decimal | None


@dataclass(frozen=True)
class Trade:
    ref: str
    out: Candidate
    into: Candidate
    cash_after: Decimal
    fp_gain: int
    salary_gain: Decimal

    def as_data(self):
        """The JSON stored on the report: what the page draws and checks against."""
        return {
            "ref": self.ref,
            "out": str(self.out.player_id),
            "in": str(self.into.player_id),
            "out_ref": self.out.ref,
            "in_ref": self.into.ref,
            "salary_out": str(self.out.salary),
            "salary_in": str(self.into.salary),
            "cash_after": str(self.cash_after),
            "fp_gain": self.fp_gain,
            "projected_out": self.out.projected_fp,
            "projected_in": self.into.projected_fp,
            "change_out": None if self.out.salary_change is None else str(self.out.salary_change),
            "change_in": None if self.into.salary_change is None else str(self.into.salary_change),
        }


def build(squad, market, cash, per_player=PER_PLAYER, limit=LIMIT):
    """The legal trades that gain projected points, best first, numbered T01...

    `squad` is the roster's players, `market` everyone who could come in
    (squad players in it are skipped). A player without a projection can go
    out (he is worth nothing to the projection) but cannot come in.
    """
    counts = {}
    for player in squad:
        counts[player.position] = counts.get(player.position, 0) + 1
    short_before = set(position_shortfalls(_with_all_positions(counts)))
    on_squad = {player.player_id for player in squad}

    options = []
    for out in squad:
        for into in market:
            if into.player_id in on_squad or into.projected_fp is None:
                continue
            gain = into.projected_fp - (out.projected_fp or 0)
            if gain <= 0:
                continue
            cash_after = cash + out.salary - into.salary
            if cash_after < 0:
                continue
            if out.position != into.position:
                after = dict(counts)
                after[out.position] -= 1
                after[into.position] = after.get(into.position, 0) + 1
                if set(position_shortfalls(_with_all_positions(after))) - short_before:
                    continue
            salary_gain = (into.salary_change or 0) - (out.salary_change or 0)
            options.append((out, into, cash_after, gain, Decimal(salary_gain)))

    options.sort(key=lambda option: (-option[3], -option[4], option[1].ref, option[0].ref))
    trades = []
    seen = Counter()
    for out, into, cash_after, gain, salary_gain in options:
        if seen["out", out.player_id] >= per_player or seen["in", into.player_id] >= per_player:
            continue
        seen["out", out.player_id] += 1
        seen["in", into.player_id] += 1
        trades.append(
            Trade(
                ref=f"T{len(trades) + 1:02d}",
                out=out,
                into=into,
                cash_after=cash_after,
                fp_gain=gain,
                salary_gain=salary_gain,
            )
        )
        if len(trades) == limit:
            break
    return trades


def _with_all_positions(counts):
    """`position_shortfalls` reads every position; one nobody holds is a zero."""
    return {position: counts.get(position, 0) for position in Player.Position.values}
