"""Correcting a past move: may it be moved to another day, or undone -- and what then?

A read model, like `timeline` and `simulator`: nothing here writes. The writes
are `services.move_transaction` and `services.undo_transaction`, which ask this
module first and refuse whatever it finds a problem with.

A move is not one record but three -- the ledger row, the roster spells it
opened and closed, and the balances on `Roster` -- and moving it in time can
break something that happened *after* it: a trade moved earlier than the free
trade it used, a sale of the player a later trade sent away. So a correction is
judged by replaying the roster's whole history with the change applied:

- **Cash and trades.** Every ledger row in order of `occurred_at` (then
  `created_at`), from the starting budget and the opening trade count. After
  each row cash must be at least zero and so must the trade count -- the same
  rule `services.trade`, `buy_trade` and `sell_trade` enforce when the move is
  first made, so no history they accepted can fail it. Free trades are ledger
  rows themselves (`GRANT_TRADE`), so a trade moved before the grant it relied
  on comes out short without a rule of its own.
- **Spells.** Who was on the roster when. A spell may not end before it starts,
  and one player may not hold two spells at once. That alone catches a trade
  moved before the outgoing player arrived, after the incoming one left again,
  or past the day the outgoing one was bought back.

Only problems the change *introduces* count. The same replay runs on the
history as it stands, and whatever is wrong there already -- a hand-edited row
from years ago -- is left out, so it cannot block an unrelated correction.

Two rules for the new date, with the user's decisions behind them:

- It keeps the move's local clock time; only the day changes.
- Prices stay as recorded while the move stays in the same salary week. Moved
  across a Sunday update, both sides are repriced from the snapshots of that
  week, as the official game would have charged them -- and with no snapshot
  for a side, the move is refused rather than priced by guesswork.

The opening trade count is whatever `Roster.trades_available` holds beyond what
the ledger explains. Rosters built while the game still started teams with two
trades have exactly that, and it is a real balance, not an error. Cash has no
such allowance: a balance the ledger does not add up to is the drift the
history page already warns about, and is for an admin to look at first.
"""

import datetime as dt
import itertools
from dataclasses import dataclass, field
from decimal import Decimal

from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.dates import local_date
from apps.core.templatetags.money import millions

from .models import (
    MINIMUM_BY_POSITION,
    PlayerSnapshot,
    RosterPlayer,
    Transaction,
    position_shortfalls,
)
from .points import cutoff
from .simulator import next_processing_day, processing_moment

CORRECTABLE = (
    Transaction.Kind.TRADE,
    Transaction.Kind.BUY_TRADE,
    Transaction.Kind.SELL_TRADE,
)

MOVE = "move"
UNDO = "undo"


# --- which moves, and which days ------------------------------------------------


def is_correctable(move, season, today=None):
    """Whether `move` is one a manager may move or undo at all.

    Trades and bought or sold trades, for as long as their season runs. Free
    trades are the game's to give, and buys and sells happen before signings
    close, where the timeline does not look. Whether a particular correction
    works is a separate question -- see `preview_move` and `preview_undo`.
    """
    today = today or timezone.localdate()
    return move.kind in CORRECTABLE and today <= season.ends_on


def _local_clock(moment):
    return timezone.localtime(moment).time()


def moved_moment(move, day):
    """`day` at the move's own local clock time: only the date changes."""
    return timezone.make_aware(dt.datetime.combine(day, _local_clock(move.occurred_at)))


def _window(move, season):
    """The first and last moment a move of this kind could have been made."""
    first = season.season_open_at
    if move.kind != Transaction.Kind.TRADE and season.trade_market_opens_at:
        first = max(first, season.trade_market_opens_at)
    first = max(first, cutoff(season))
    last = season.season_close_at
    if move.kind != Transaction.Kind.TRADE and season.trade_market_closes_at:
        last = min(last, season.trade_market_closes_at - dt.timedelta(microseconds=1))
    return first, last


def day_range(move, season, now=None):
    """The first and last day the date picker offers, or None when there is none.

    The last is today when the move's clock time has already passed today, and
    yesterday otherwise: a correction may not put a move in the future.
    """
    now = now or timezone.now()
    first, last = _window(move, season)
    last = min(last, now)
    first_day = timezone.localdate(first)
    if moved_moment(move, first_day) < first:
        first_day += dt.timedelta(days=1)
    last_day = timezone.localdate(last)
    if moved_moment(move, last_day) > last:
        last_day -= dt.timedelta(days=1)
    return (first_day, last_day) if first_day <= last_day else None


def _date_problem(move, season, moment, now):
    if moment > now:
        return _("That day has not happened yet.")
    first, last = _window(move, season)
    if move.kind == Transaction.Kind.TRADE:
        if moment < first or moment > last:
            return _("Trades can only be dated while the season is running.")
    elif moment < first or moment > last:
        return _("Trades can only be bought and sold while the trade market is open.")
    return ""


# --- prices ---------------------------------------------------------------------


def batch_moment(moment):
    """When the batch that processes a move made at `moment` runs."""
    return processing_moment(next_processing_day(moment))


def salary_update_before(season, moment):
    """The last Sunday salary update before the batch that processes `moment`.

    None before the first one. A move on a Sunday afternoon is processed at
    22:00 with the old salaries, so the update that evening does not count.
    """
    batch = batch_moment(moment)
    return max((u for u in season.salary_update_moments() if u < batch), default=None)


def salary_at(player, season, moment):
    """What `player` cost in the batch that processes `moment`, or None.

    The newest snapshot taken after that week's update and before the batch.
    None when the import missed that week for him: no number is better than
    last week's dressed up as this week's.
    """
    batch = batch_moment(moment)
    snapshots = PlayerSnapshot.objects.filter(player=player, season=season, as_of__lt=batch)
    update = salary_update_before(season, moment)
    if update is not None:
        snapshots = snapshots.filter(as_of__gte=update)
    return snapshots.order_by("-as_of").values_list("salary", flat=True).first()


# --- the replay -----------------------------------------------------------------


@dataclass(frozen=True)
class _Row:
    """One ledger row as the replay reads it."""

    move: Transaction
    at: dt.datetime
    cash_delta: Decimal
    trades: int


def _trade_effect(move):
    if move.kind == Transaction.Kind.GRANT_TRADE:
        return move.trades_granted or 0
    if move.kind == Transaction.Kind.BUY_TRADE:
        return 1
    if move.kind in (Transaction.Kind.SELL_TRADE, Transaction.Kind.TRADE):
        return -1
    return 0


@dataclass
class _Span:
    """One spell as the replay reads it: a copy, so a what-if never touches the row."""

    spell: RosterPlayer
    start: dt.datetime
    end: dt.datetime | None


def describe(move):
    """`Trade Brunson → Ball`, `Bought trade`: how a problem names a move."""
    if move.kind == Transaction.Kind.TRADE:
        return _("Trade %(out)s → %(in)s") % {
            "out": move.player_out.last_name if move.player_out else "?",
            "in": move.player_in.last_name if move.player_in else "?",
        }
    if move.kind == Transaction.Kind.BUY_TRADE:
        return _("Bought trade")
    if move.kind == Transaction.Kind.SELL_TRADE:
        return _("Sold trade")
    if move.kind == Transaction.Kind.BUY:
        return _("Signing of %(player)s") % {"player": move.player_in}
    if move.kind == Transaction.Kind.SELL:
        return _("Release of %(player)s") % {"player": move.player_out}
    return move.get_kind_display()


def _replay_ledger(rows, starting_cash, opening_trades, edited):
    """Problems in the cash and trade balances, keyed so a baseline can be removed."""
    problems = {}
    cash, trades = starting_cash, opening_trades
    for row in sorted(rows, key=lambda r: (r.at, r.move.created_at)):
        cash += row.cash_delta
        trades += row.trades
        is_edited = edited is not None and row.move.pk == edited.pk
        what = _("This move") if is_edited else describe(row.move)
        when = local_date(row.at)
        if trades < 0:
            if row.move.kind == Transaction.Kind.SELL_TRADE:
                message = _("%(what)s on %(date)s would sell a trade the roster does not have.")
            else:
                message = _("%(what)s on %(date)s would have no trade available.")
            problems[("trades", row.move.pk)] = message % {"what": what, "date": when}
        if cash < 0:
            problems[("cash", row.move.pk)] = _(
                "%(what)s on %(date)s would leave cash at %(cash)s."
            ) % {"what": what, "date": when, "cash": millions(cash)}
    return problems


def _replay_spells(spans):
    """Problems in who was on the roster when."""
    problems = {}
    by_player = {}
    for span in spans:
        by_player.setdefault(span.spell.player_id, []).append(span)
        if span.end is not None and span.end < span.start:
            problems[("backwards", span.spell.pk)] = _(
                "%(player)s would leave the roster before joining it (%(date)s)."
            ) % {"player": span.spell.player, "date": local_date(span.start)}
    for spells in by_player.values():
        spells.sort(key=lambda s: (s.start, s.spell.created_at))
        for earlier, later in itertools.pairwise(spells):
            if earlier.end is None or earlier.end > later.start:
                problems[("twice", later.spell.pk)] = _(
                    "%(player)s would be on the roster twice at once (%(date)s)."
                ) % {"player": later.spell.player, "date": local_date(later.start)}
    return problems


def _shortfalls(spans, moments, positions):
    """The position gaps at each of `moments`, as a dict of moment -> set of gaps."""
    result = {}
    for moment in moments:
        counts = dict.fromkeys(MINIMUM_BY_POSITION, 0)
        for span in spans:
            if span.start <= moment and (span.end is None or span.end > moment):
                position = positions.get(span.spell.player_id)
                if position in counts:
                    counts[position] += 1
        result[moment] = set(position_shortfalls(counts))
    return result


# --- the verdict ----------------------------------------------------------------


@dataclass
class Correction:
    """What a correction would do, and whether it may."""

    move: Transaction
    action: str
    occurred_at_after: dt.datetime | None = None
    price_out_after: Decimal | None = None
    price_in_after: Decimal | None = None
    cash_delta_after: Decimal | None = None
    # The Sunday update the new date crosses, when the move was repriced.
    repriced_by: dt.datetime | None = None
    cash_before: Decimal = Decimal("0")
    cash_after: Decimal = Decimal("0")
    trades_before: int = 0
    trades_after: int = 0
    problems: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    # The spells the write has to change, found once here.
    out_spell: RosterPlayer | None = None
    in_spell: RosterPlayer | None = None

    @property
    def ok(self):
        return not self.problems

    @property
    def repriced(self):
        return self.repriced_by is not None

    @property
    def cash_change(self):
        return self.cash_after - self.cash_before

    @property
    def trades_change(self):
        return self.trades_after - self.trades_before


class _History:
    """A roster's ledger and spells, loaded once for any number of what-ifs."""

    def __init__(self, roster):
        self.roster = roster
        self.season = roster.season
        self.moves = list(roster.transactions.select_related("player_in", "player_out"))
        self.spells = list(
            RosterPlayer.objects.filter(roster=roster).select_related("player").order_by("pk")
        )
        self.ledger_cash = roster.starting_cash + sum(
            (move.cash_delta for move in self.moves), Decimal("0")
        )
        self.opening_trades = roster.trades_available - sum(
            _trade_effect(move) for move in self.moves
        )
        self.start = cutoff(self.season)

    def adds_up(self):
        return self.ledger_cash == self.roster.cash and self.opening_trades >= 0

    def rows(self, changed=None, removed=None):
        rows = []
        for move in self.moves:
            if removed is not None and move.pk == removed.pk:
                continue
            at, delta = move.occurred_at, move.cash_delta
            if changed is not None and move.pk == changed[0].pk:
                at, delta = changed[1], changed[2]
            rows.append(_Row(move=move, at=at, cash_delta=delta, trades=_trade_effect(move)))
        return rows

    def spans(self, overrides=None, dropped=None):
        overrides = overrides or {}
        return [
            _Span(spell=spell, start=start, end=end)
            for spell in self.spells
            if spell.pk != dropped
            for start, end in [overrides.get(spell.pk, (spell.added_at, spell.removed_at))]
        ]

    def trade_spells(self, move):
        """The outgoing and incoming spell `services.trade` wrote for `move`."""
        out = [
            s
            for s in self.spells
            if s.player_id == move.player_out_id and s.removed_at == move.occurred_at
        ]
        in_ = [
            s
            for s in self.spells
            if s.player_id == move.player_in_id and s.added_at == move.occurred_at
        ]
        if len(out) != 1 or len(in_) != 1:
            return None, None
        return out[0], in_[0]


def _verdict(history, correction, rows, spans, edited):
    """Fill in the balances and the problems the change introduces."""
    roster = history.roster
    correction.cash_before = roster.cash
    correction.trades_before = roster.trades_available
    correction.cash_after = roster.starting_cash + sum((r.cash_delta for r in rows), Decimal("0"))
    correction.trades_after = history.opening_trades + sum(r.trades for r in rows)

    baseline = _replay_ledger(history.rows(), roster.starting_cash, history.opening_trades, None)
    baseline |= _replay_spells(history.spans())
    after = _replay_ledger(rows, roster.starting_cash, history.opening_trades, edited)
    after |= _replay_spells(spans)
    correction.problems += [message for key, message in after.items() if key not in baseline]


def _refuse(correction, message):
    correction.problems.append(message)
    return correction


def _base(history, move, action, now):
    correction = Correction(move=move, action=action)
    correction.cash_before = correction.cash_after = history.roster.cash
    correction.trades_before = correction.trades_after = history.roster.trades_available
    if not history.adds_up():
        return _refuse(
            correction,
            _(
                "This roster's records do not add up, so its history cannot be changed. "
                "Please ask an admin to have a look."
            ),
        )
    if not is_correctable(move, history.season, timezone.localdate(now)):
        return _refuse(correction, _("This move can no longer be changed."))
    if move.kind == Transaction.Kind.TRADE:
        correction.out_spell, correction.in_spell = history.trade_spells(move)
        if correction.out_spell is None:
            return _refuse(
                correction,
                _(
                    "This trade's records do not line up, so it cannot be changed here. "
                    "Please ask an admin to have a look."
                ),
            )
    return correction


def preview_move(roster, move, day, now=None):
    """What moving `move` to `day` would do. A `Correction`; `ok` says if it may."""
    now = now or timezone.now()
    history = _History(roster)
    correction = _base(history, move, MOVE, now)
    if not correction.ok:
        return correction

    moment = moved_moment(move, day)
    correction.occurred_at_after = moment
    correction.price_out_after, correction.price_in_after = move.price_out, move.price_in
    correction.cash_delta_after = move.cash_delta
    if timezone.localdate(moment) == timezone.localdate(move.occurred_at):
        return _refuse(correction, _("Pick a different day: the move is already on this one."))
    if problem := _date_problem(move, history.season, moment, now):
        return _refuse(correction, problem)

    if move.kind == Transaction.Kind.TRADE:
        update = salary_update_before(history.season, moment)
        if update != salary_update_before(history.season, move.occurred_at):
            price_out = salary_at(move.player_out, history.season, moment)
            price_in = salary_at(move.player_in, history.season, moment)
            for player, price in ((move.player_out, price_out), (move.player_in, price_in)):
                if price is None:
                    return _refuse(
                        correction,
                        _("No salary on record for %(player)s in the week of %(date)s.")
                        % {"player": player, "date": local_date(moment)},
                    )
            correction.price_out_after, correction.price_in_after = price_out, price_in
            correction.cash_delta_after = price_out - price_in
            # The update the move crosses: the later of the two weeks' updates.
            old = salary_update_before(history.season, move.occurred_at)
            correction.repriced_by = max(u for u in (update, old) if u is not None)

    rows = history.rows(changed=(move, moment, correction.cash_delta_after))
    overrides = {}
    if correction.out_spell is not None:
        overrides[correction.out_spell.pk] = (correction.out_spell.added_at, moment)
        overrides[correction.in_spell.pk] = (moment, correction.in_spell.removed_at)
    spans = history.spans(overrides)
    _verdict(history, correction, rows, spans, move)
    if move.kind == Transaction.Kind.TRADE and correction.ok:
        _warn_positions(history, correction, spans, sorted([move.occurred_at, moment]))
    return correction


def preview_undo(roster, move, now=None):
    """What undoing `move` would do. A `Correction`; `ok` says if it may."""
    now = now or timezone.now()
    history = _History(roster)
    correction = _base(history, move, UNDO, now)
    if not correction.ok:
        return correction

    overrides, dropped = {}, None
    if move.kind == Transaction.Kind.TRADE:
        incoming = correction.in_spell
        if incoming.removed_at is not None:
            return _refuse(
                correction,
                _("%(player)s has left the roster again since (%(date)s). Undo that move first.")
                % {"player": incoming.player, "date": local_date(incoming.removed_at)},
            )
        overrides[correction.out_spell.pk] = (correction.out_spell.added_at, None)
        dropped = incoming.pk

    rows = history.rows(removed=move)
    spans = history.spans(overrides, dropped)
    _verdict(history, correction, rows, spans, None)
    if move.kind == Transaction.Kind.TRADE and correction.ok:
        _warn_positions(history, correction, spans, [move.occurred_at, now])
    return correction


def _warn_positions(history, correction, spans, between):
    """Position minimums the change newly leaves short, somewhere in `between`.

    A warning, not a problem: `services.trade` does not enforce the minimums
    either, so a history may already pass through a short day, and refusing the
    correction for one would be stricter than making the move was.
    """
    first, last = between
    first = max(first, history.start)
    moments = {first, last}
    for span in [*spans, *history.spans()]:
        for moment in (span.start, span.end):
            if moment is not None and first <= moment <= last:
                moments.add(moment)
    moments = sorted(m for m in moments if m <= last)
    positions = {spell.player_id: spell.player.position for spell in history.spells}
    before = _shortfalls(history.spans(), moments, positions)
    after = _shortfalls(spans, moments, positions)
    seen = set()
    for moment in moments:
        for gap in sorted(after[moment] - before[moment]):
            if gap not in seen:
                seen.add(gap)
                correction.warnings.append(
                    _("From %(date)s the roster would be short: %(gap)s.")
                    % {"date": local_date(moment), "gap": gap}
                )
