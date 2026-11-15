"""The legal trades a roster could make right now, best first.

What the AI chooses from: it may only recommend a trade on this list, so a
recommendation can never break the game's rules. A trade swaps one squad
player for one market player at today's salaries; it is legal when the cash
covers it and it breaks no position minimum the roster meets today (the same
rule as the trade screen's `trade_preview`).

Ranked by projected rest-of-season points gained, with the predicted salary
gain at the next update as the tiebreak: points decide the standings, cash only
pays for later trades.

The projection uses the season's points per game, which lags a player whose
role grew lately: his season-based gain is small or below zero, and he would
never make the list. So up to `RESERVED` places are kept for trades bringing in
a *rising* player (`Candidate.riser`), ranked by the gain on recent form
instead. They push out the lowest-ranked trades, never better ones.

Pure arithmetic over `Candidate`s, no database.
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
# Places kept for trades bringing in a rising player.
RESERVED = 8


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
    # The same projection on recent form, None without enough recent games.
    recent_projected_fp: int | None = None
    # Why he counts as rising ("form", "opening", "form+opening"), or None.
    riser: str | None = None

    @property
    def recent_or_season(self):
        """The recent-form projection when there is one, else the season's."""
        return (
            self.recent_projected_fp
            if self.recent_projected_fp is not None
            else (self.projected_fp)
        )


@dataclass(frozen=True)
class Trade:
    ref: str
    out: Candidate
    into: Candidate
    cash_after: Decimal
    fp_gain: int
    salary_gain: Decimal
    # The gain if both players keep their recent form.
    fp_gain_recent: int | None = None

    @property
    def riser(self):
        return self.into.riser

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
            "fp_gain_recent": self.fp_gain_recent,
            "riser": self.riser,
            "projected_out": self.out.projected_fp,
            "projected_in": self.into.projected_fp,
            "change_out": None if self.out.salary_change is None else str(self.out.salary_change),
            "change_in": None if self.into.salary_change is None else str(self.into.salary_change),
        }


@dataclass(frozen=True)
class _Option:
    out: Candidate
    into: Candidate
    cash_after: Decimal
    gain: int
    recent_gain: int | None
    salary_gain: Decimal

    @property
    def pair(self):
        return self.out.player_id, self.into.player_id


def build(squad, market, cash, per_player=PER_PLAYER, limit=LIMIT, reserved=RESERVED):
    """The legal trades that gain projected points, best first, numbered T01...

    `squad` is the roster's players, `market` everyone who could come in
    (squad players in it are skipped). A player without a projection can go
    out (he is worth nothing to the projection) but cannot come in. Up to
    `reserved` places go to trades bringing in a riser that gain on recent
    form, after the trades ranked on the season.
    """
    options = _legal_options(squad, market, cash)
    main = sorted(
        (option for option in options if option.gain > 0),
        key=lambda option: (-option.gain, -option.salary_gain, option.into.ref, option.out.ref),
    )
    seen = Counter()
    chosen = _pick(main, limit, per_player, seen)

    taken = {option.pair for option in chosen}
    risers_in = sum(1 for option in chosen if option.into.riser)
    rising = sorted(
        (
            option
            for option in options
            if option.into.riser
            and option.recent_gain is not None
            and option.recent_gain > 0
            and option.pair not in taken
        ),
        key=lambda option: (
            -option.recent_gain,
            -option.salary_gain,
            option.into.ref,
            option.out.ref,
        ),
    )
    chosen, extra = _add_risers(
        chosen, rising, max(reserved - risers_in, 0), limit, per_player, seen
    )

    return [
        Trade(
            ref=f"T{number:02d}",
            out=option.out,
            into=option.into,
            cash_after=option.cash_after,
            fp_gain=option.gain,
            salary_gain=option.salary_gain,
            fp_gain_recent=option.recent_gain,
        )
        for number, option in enumerate([*chosen, *extra], start=1)
    ]


def _legal_options(squad, market, cash):
    """Every swap the rules allow that brings in a player with a projection."""
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
            cash_after = cash + out.salary - into.salary
            if cash_after < 0:
                continue
            if out.position != into.position:
                after = dict(counts)
                after[out.position] -= 1
                after[into.position] = after.get(into.position, 0) + 1
                if set(position_shortfalls(_with_all_positions(after))) - short_before:
                    continue
            recent_in = into.recent_or_season
            recent_gain = None if recent_in is None else recent_in - (out.recent_or_season or 0)
            options.append(
                _Option(
                    out=out,
                    into=into,
                    cash_after=cash_after,
                    gain=into.projected_fp - (out.projected_fp or 0),
                    recent_gain=recent_gain,
                    salary_gain=Decimal((into.salary_change or 0) - (out.salary_change or 0)),
                )
            )
    return options


def _add_risers(chosen, rising, count, limit, per_player, seen):
    """Up to `count` riser trades, each pushing out the lowest-ranked trade
    that brings in no riser once the list is full. A pushed-out trade frees
    its players' places under `per_player`."""
    chosen, extra = list(chosen), []
    for option in rising:
        if len(extra) >= count:
            break
        drop = None
        if len(chosen) + len(extra) >= limit:
            drop = next(
                (i for i in range(len(chosen) - 1, -1, -1) if not chosen[i].into.riser), None
            )
            if drop is None:
                break
        freed = chosen[drop] if drop is not None else None
        out_count = seen["out", option.out.player_id] - (
            freed is not None and freed.out.player_id == option.out.player_id
        )
        in_count = seen["in", option.into.player_id] - (
            freed is not None and freed.into.player_id == option.into.player_id
        )
        if out_count >= per_player or in_count >= per_player:
            continue
        if freed is not None:
            chosen.pop(drop)
            seen["out", freed.out.player_id] -= 1
            seen["in", freed.into.player_id] -= 1
        seen["out", option.out.player_id] += 1
        seen["in", option.into.player_id] += 1
        extra.append(option)
    return chosen, extra


def _pick(options, count, per_player, seen):
    """The first `count` options that keep each player within `per_player` trades."""
    picked = []
    for option in options:
        if len(picked) >= count:
            break
        if (
            seen["out", option.out.player_id] >= per_player
            or seen["in", option.into.player_id] >= per_player
        ):
            continue
        seen["out", option.out.player_id] += 1
        seen["in", option.into.player_id] += 1
        picked.append(option)
    return picked


def _with_all_positions(counts):
    """`position_shortfalls` reads every position; one nobody holds is a zero."""
    return {position: counts.get(position, 0) for position in Player.Position.values}
