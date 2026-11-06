"""Who was on a roster when: the data behind the roster timeline.

A read model, kept out of `services` because nothing here writes. It turns a
roster's spells (`RosterPlayer`) and trades (`Transaction`) into fifteen
*spots*: one lane per place on the roster, each a sequence of the players who
held it, cut wherever a trade swapped one for the next.

The timeline starts with the squad as it stood when signings closed. Everything
bought and sold before that is left out on purpose: it is the manager trying
things, not players who ever scored for the roster, and the moves table on the
same page still lists all of it.

The spells are the truth about *when*; the trades only say which spell
followed which. That way a hand-corrected date on one side cannot leave a
player on two lanes or none -- at worst a link is missing and the incoming
player takes the first free lane.
"""

import bisect
import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone
from django.utils.formats import number_format

from apps.core.templatetags.money import millions
from apps.nba.templatetags.player_stats import short_month_day

from .models import PlayerSnapshot, Transaction

POSITION_ORDER = {"G": 0, "F": 1, "C": 2}


@dataclass
class Stint:
    membership: object
    start: dt.datetime  # the moment he started counting for the roster
    end: dt.datetime | None  # None while he is still on it
    came_by: Transaction | None = None
    left_by: Transaction | None = None
    traded_for: "Stint | None" = None  # the spell this one replaced
    lane: int | None = None
    points: Decimal | None = None


@dataclass
class Timeline:
    """Everything the chart needs, already in JSON-friendly shapes."""

    starts_on: dt.date
    ends_on: dt.date
    today: dt.date
    trade_free_until: dt.date | None
    spots: list = field(default_factory=list)
    trade_count: int = 0

    def as_json(self):
        return {
            "startsOn": self.starts_on.isoformat(),
            "endsOn": self.ends_on.isoformat(),
            "today": self.today.isoformat(),
            "tradeFreeUntil": self.trade_free_until.isoformat() if self.trade_free_until else None,
            "tradeCount": self.trade_count,
            "spots": self.spots,
        }


def cutoff(season):
    """The moment the starting squad is read: when signings closed.

    A season without a cutoff keeps signings open all season, so there is no
    pre-season to leave out and its first day stands in.
    """
    return season.signings_close_at or season.season_open_at


def roster_timeline(roster, now=None):
    """The roster's spots from the starting squad to `now`, or None before then."""
    now = now or timezone.now()
    season = roster.season
    start_moment = cutoff(season)
    if now < start_moment:
        return None

    spells = [
        spell
        for spell in roster.memberships.select_related("player").order_by("added_at", "created_at")
        if spell.added_at <= now and (spell.removed_at is None or spell.removed_at > start_moment)
    ]
    stints = [
        Stint(
            membership=spell,
            start=max(spell.added_at, start_moment),
            end=spell.removed_at if spell.removed_at and spell.removed_at <= now else None,
        )
        for spell in spells
    ]
    trades = list(
        roster.transactions.filter(
            kind=Transaction.Kind.TRADE, occurred_at__gt=start_moment, occurred_at__lte=now
        )
        .select_related("player_in", "player_out")
        .order_by("occurred_at", "created_at")
    )
    _link_trades(stints, trades)
    _assign_lanes(stints)
    _add_points(stints, season, now)

    timeline = Timeline(
        starts_on=min(season.starts_on, timezone.localdate(start_moment)),
        ends_on=season.ends_on,
        today=timezone.localdate(now),
        trade_free_until=timezone.localdate(season.trade_market_opens_at)
        if season.trade_market_opens_at
        else None,
        trade_count=len(trades),
    )
    timeline.spots = _spots(stints, start_moment)
    return timeline


def _link_trades(stints, trades):
    """Pair each trade's outgoing spell with its incoming one.

    Matched on the exact moment first, because that is how `services.trade`
    writes them, and on the nearest spell second, for rows whose dates were
    corrected by hand on one side only.
    """
    by_player = {}
    for stint in stints:
        by_player.setdefault(stint.membership.player_id, []).append(stint)

    for trade in trades:
        at = trade.occurred_at
        leaving = [s for s in by_player.get(trade.player_out_id, []) if s.left_by is None]
        out = next((s for s in leaving if s.end == at), None) or next(
            (s for s in leaving if s.start < at and (s.end is None or s.end >= at)), None
        )
        arriving = [s for s in by_player.get(trade.player_in_id, []) if s.came_by is None]
        incoming = next((s for s in arriving if s.membership.added_at == at), None) or next(
            (s for s in arriving if s.membership.added_at >= at), None
        )
        if out is not None:
            out.left_by = trade
        if incoming is not None:
            incoming.came_by = trade
            incoming.traded_for = out


def _assign_lanes(stints):
    """Starting squad on lanes 0-14; everyone after takes the lane he was traded into."""
    lanes = []  # last stint per lane
    for stint in sorted(stints, key=lambda s: (s.start, s.membership.created_at)):
        predecessor = stint.traded_for
        if predecessor is not None and predecessor.lane is not None:
            stint.lane = predecessor.lane
        else:
            stint.lane = next(
                (
                    index
                    for index, last in enumerate(lanes)
                    if last.end is not None and last.end <= stint.start
                ),
                len(lanes),
            )
        if stint.lane == len(lanes):
            lanes.append(stint)
        else:
            lanes[stint.lane] = stint


def _add_points(stints, season, now):
    """What each player scored while he was on the roster, from the snapshots.

    The season total at the end of the spell minus the total at its start. The
    snapshots are daily, so this is as precise as they are -- the screen says
    "approx." for that reason. A player with no snapshot before a mid-season
    arrival gets no number rather than a wrong one.
    """
    player_ids = {s.membership.player_id for s in stints}
    series = {}
    for player_id, as_of, total in (
        PlayerSnapshot.objects.filter(season=season, player_id__in=player_ids, as_of__lte=now)
        .order_by("player_id", "as_of")
        .values_list("player_id", "as_of", "total_fp")
    ):
        moments, totals = series.setdefault(player_id, ([], []))
        moments.append(as_of)
        totals.append(total)

    def total_at(player_id, moment):
        moments, totals = series.get(player_id, ([], []))
        index = bisect.bisect_right(moments, moment) - 1
        return totals[index] if index >= 0 else None

    for stint in stints:
        player_id = stint.membership.player_id
        if player_id not in series:
            continue
        before = total_at(player_id, stint.start)
        if before is None:
            # Nothing recorded yet when he arrived: zero if that was before
            # the season's first game, unknown otherwise.
            if stint.start > season.season_open_at:
                continue
            before = Decimal("0")
        after = total_at(player_id, stint.end or now)
        if after is not None:
            stint.points = max(Decimal("0"), after - before)


def _move(trade, price, partner):
    """One side of a trade as the details panel tells it."""
    day = timezone.localdate(trade.occurred_at)
    return {
        "dateLabel": short_month_day(day),
        "partner": partner.full_name if partner else "",
        "price": millions(price) if price is not None else "",
    }


def _spots(stints, start_moment):
    lanes = {}
    for stint in stints:
        lanes.setdefault(stint.lane, []).append(stint)

    spots = []
    for lane in lanes.values():
        lane.sort(key=lambda s: s.start)
        current = lane[-1] if lane[-1].end is None else None
        spots.append((current, lane))

    def order(item):
        current, lane = item
        if current is None:
            return (1, 9, lane[-1].end)
        return (0, POSITION_ORDER.get(current.membership.player.position, 9), current.start)

    spots.sort(key=order)

    return [{"stints": [_stint_json(stint, start_moment) for stint in lane]} for _, lane in spots]


def _stint_json(stint, start_moment):
    player = stint.membership.player
    from_start = stint.start <= start_moment
    end_day = timezone.localdate(stint.end) if stint.end else None
    initial = player.first_name[:1]
    return {
        "name": player.full_name,
        "short": f"{initial}. {player.last_name}" if initial else player.last_name,
        "pos": player.position,
        "url": reverse("nba:player-detail", args=[player.slug]) if player.slug else "",
        # None: there from the first day of the chart.
        "start": None if from_start else timezone.localdate(stint.start).isoformat(),
        "end": end_day.isoformat() if end_day else None,
        "came": _move(stint.came_by, stint.came_by.price_in, stint.came_by.player_out)
        if stint.came_by
        else None,
        "left": _move(stint.left_by, stint.left_by.price_out, stint.left_by.player_in)
        if stint.left_by
        else None,
        "points": number_format(stint.points.quantize(Decimal("1")), force_grouping=True)
        if stint.points is not None
        else None,
    }
