"""Replay a trade plan against the roster as it stands: does it work, and how.

A read model like `timeline`, and for the same reason kept out of `services`:
nothing here writes. A plan stores steps only -- no prices, no balances -- so
every figure below is worked out afresh from the live roster, today's salaries
and the salary outlook each time a plan is looked at. That is what keeps a
variant written on Friday right on Sunday without anyone touching it.

The official game's rules, as this module reads them:

- Confirmed trades are processed once a day at 22:00 (`TRADE_PROCESSING_TIME`)
  as one batch. Cash and the position minimums are therefore checked once per
  processing day, after all of that day's steps -- a day may dip below zero
  in between as long as it ends at or above it.
- Salaries reprice on Sundays at 22:00, and a trade processed then already
  uses the *new* salaries. So a step is priced at the salaries of the latest
  update at or before its processing moment: today's if no update lies in
  between (`KNOWN`), the salary outlook's prediction for the next one
  (`PREDICTED`), and the same prediction, less trustworthy, beyond that
  (`PREDICTED_FAR`) -- the outlook only looks one update ahead.
- Free trades granted at a moment are usable from the next processing after
  it. The Sunday 22:00 weekly trade arrives with that evening's batch, not in
  time for it.
- A trade can be bought (1.5M) or sold (1.0M) while the season's trade market
  is open.

Steps the roster has already made -- a matching `Transaction` written after
the step was planned -- count as done and are skipped. A step on a processing
day that has passed without one was missed. Neither is replayed: the live
roster already reflects whatever really happened.
"""

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Prefetch
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from apps.core.templatetags.money import millions
from apps.nba.models import Player, PlayerInjury
from apps.nba.salary_outlook import salary_baseline, salary_outlook

from .models import (
    TRADE_BUY_PRICE,
    TRADE_PROCESSING_TIME,
    TRADE_SELL_PRICE,
    PlanStep,
    PlayerSnapshot,
    Transaction,
    position_shortfalls,
)

KNOWN = "known"
PREDICTED = "predicted"
PREDICTED_FAR = "predicted_far"

# What a step came to.
OK = "ok"
STALE = "stale"  # cannot happen as written: someone is not where it expects
DONE = "done"  # already made on the roster
MISSED = "missed"  # its day has passed and it was never made

ZERO = Decimal("0")


def processing_moment(day):
    """When the game processes the trades confirmed for `day`."""
    return timezone.make_aware(dt.datetime.combine(day, TRADE_PROCESSING_TIME))


def next_processing_day(now=None):
    """The first day whose batch is still ahead: today until 22:00, then tomorrow."""
    now = now or timezone.now()
    today = timezone.localdate(now)
    return today if now < processing_moment(today) else today + dt.timedelta(days=1)


@dataclass
class StepResult:
    step: PlanStep
    status: str = OK
    reason: str = ""
    price_out: Decimal | None = None
    price_in: Decimal | None = None
    cash_delta: Decimal = ZERO

    @property
    def is_trade(self):
        return self.step.kind == PlanStep.Kind.TRADE

    @property
    def counts(self):
        """Whether this step moves the simulation."""
        return self.status == OK


@dataclass
class DayResult:
    date: dt.date
    moment: dt.datetime
    price_source: str
    is_past: bool
    steps: list = field(default_factory=list)
    grants: list = field(default_factory=list)  # TradeGrantDue arriving before the batch
    squad_before: list = field(default_factory=list)
    squad_after: list = field(default_factory=list)
    cash_before: Decimal = ZERO
    cash_after: Decimal = ZERO
    trades_before: int = 0
    trades_after: int = 0
    problems: list = field(default_factory=list)
    trades_short: int = 0
    counts_after: dict = field(default_factory=dict)

    @property
    def ok(self):
        return not self.problems and all(step.status in (OK, DONE) for step in self.steps)

    @property
    def is_predicted(self):
        return self.price_source != KNOWN

    @property
    def cash_flow(self):
        return self.cash_after - self.cash_before

    @property
    def granted(self):
        return sum(grant.trades for grant in self.grants)


@dataclass
class PlanResult:
    plan: object
    now: dt.datetime
    days: list
    cash_start: Decimal
    trades_start: int
    squad_start: list
    team_value_start: Decimal
    squad_end: list = field(default_factory=list)
    cash_end: Decimal = ZERO
    trades_end: int = 0
    team_value_end: Decimal = ZERO
    trades_used: int = 0
    trades_bought: int = 0
    trades_sold: int = 0
    players: dict = field(default_factory=dict)  # every player the plan touches, by pk
    grants: list = field(default_factory=list)  # free trades still to come after `now`

    @property
    def future_days(self):
        return [day for day in self.days if not day.is_past]

    @property
    def step_results(self):
        return [step for day in self.days for step in day.steps]

    @property
    def stale_steps(self):
        return [step for step in self.step_results if step.status == STALE]

    @property
    def missed_steps(self):
        return [step for step in self.step_results if step.status == MISSED]

    @property
    def done_steps(self):
        return [step for step in self.step_results if step.status == DONE]

    @property
    def first_failure(self):
        return next((day for day in self.future_days if not day.ok), None)

    @property
    def works(self):
        """Whether every step still ahead can be made as planned."""
        return self.first_failure is None

    @property
    def has_steps(self):
        return bool(self.step_results)

    @property
    def has_predicted_prices(self):
        return any(day.is_predicted and day.steps for day in self.future_days)

    @property
    def cash_change(self):
        return self.cash_end - self.cash_start

    @property
    def team_value_change(self):
        return self.team_value_end - self.team_value_start

    def state_before(self, day):
        """(squad, cash, trades) going into `day`'s batch, before its own steps.

        For a day with no steps in the plan, too -- the step editor offers any
        upcoming day, and needs the roster as it would stand by then, including
        the free trades that will have arrived in the meantime.
        """
        squad, cash, trades = self.squad_start, self.cash_start, self.trades_start
        since = self.now
        for result in self.days:
            if result.date == day:
                return result.squad_before, result.cash_before, result.trades_before
            if result.date > day:
                break
            squad, cash, trades = result.squad_after, result.cash_after, result.trades_after
            since = result.moment
        moment = processing_moment(day)
        trades += sum(grant.trades for grant in self.grants if since <= grant.granted_at < moment)
        return squad, cash, trades


class Prices:
    """What a player is worth at a processing moment, by the rules above."""

    def __init__(self, season, now):
        self.baseline = salary_baseline(season, now=now)
        if self.baseline is None:
            self.updates = []
        else:
            read = self.baseline.moment if not self.baseline.is_preseason else None
            self.updates = [
                moment for moment in season.salary_update_moments() if read is None or moment > read
            ]
        self._predicted = {}

    def source(self, moment):
        ahead = sum(1 for update in self.updates if update <= moment)
        if ahead == 0:
            return KNOWN
        return PREDICTED if ahead == 1 else PREDICTED_FAR

    def price(self, player, source):
        """In dollars, or None when the player has no salary on record."""
        if player.current_salary is None:
            return None
        if source == KNOWN:
            return player.current_salary
        if player.pk not in self._predicted:
            outlook = salary_outlook(player, self.baseline)
            # No prediction (no games yet, no baseline snapshot): today's price
            # is the best guess there is, and the day says it is a guess.
            self._predicted[player.pk] = outlook.salary if outlook else player.current_salary
        return self._predicted[player.pk]


def _players(ids):
    """The players a plan names, loaded the way the outlook and the screens read them."""
    return {
        player.pk: player
        for player in Player.objects.filter(pk__in=ids)
        .select_related("team")
        .prefetch_related(
            Prefetch("snapshots", queryset=PlayerSnapshot.objects.order_by("-as_of")),
            Prefetch(
                "injuries",
                queryset=PlayerInjury.objects.order_by("-observed_at", "-created_at"),
                to_attr="injury_history",
            ),
        )
    }


def _unpaid_grants(roster):
    """Every grant this roster is still to receive, including any already due.

    Read rather than paid: the screens pay due grants when they load a roster,
    but a simulation must not write, and must be right without them.
    """
    paid = set(
        roster.transactions.filter(kind=Transaction.Kind.GRANT_TRADE).values_list(
            "grant_key", flat=True
        )
    )
    return [grant for grant in roster.season.trade_grant_schedule() if grant.key not in paid]


class _Made:
    """The roster's real moves since a plan was begun, to tell what is done.

    Each real move can mark one step done, so two planned trade purchases are
    not both satisfied by a single bought trade.
    """

    def __init__(self, roster, since):
        self.moves = (
            list(
                roster.transactions.filter(
                    occurred_at__gte=since,
                    kind__in=[
                        Transaction.Kind.TRADE,
                        Transaction.Kind.BUY_TRADE,
                        Transaction.Kind.SELL_TRADE,
                    ],
                ).values("pk", "kind", "player_out_id", "player_in_id", "occurred_at")
            )
            if since
            else []
        )

    def claim(self, step):
        for move in self.moves:
            if (
                move["kind"] == step.kind
                and move["occurred_at"] >= step.created_at
                and move["player_out_id"] == step.player_out_id
                and move["player_in_id"] == step.player_in_id
            ):
                self.moves.remove(move)
                return True
        return False


def simulate(plan, now=None, extra_steps=(), steps=None):
    """Replay `plan` against its roster. Returns a `PlanResult`.

    `extra_steps` are unsaved steps replayed as if they were part of the plan
    -- how the step editor previews a step before it is added. `steps` replaces
    the plan's own (already loaded, say); by default they are read here.
    """
    now = now or timezone.now()
    roster = plan.roster
    season = roster.season
    steps = list(steps if steps is not None else plan.steps.all())
    steps += list(extra_steps)
    steps.sort(key=lambda step: (step.processing_date, step.order, step.created_at or now))

    memberships = roster.current_memberships
    squad_ids = [membership.player_id for membership in memberships]
    named = {
        pk for step in steps for pk in (step.player_out_id, step.player_in_id) if pk is not None
    }
    players = {membership.player_id: membership.player for membership in memberships}
    missing = named - players.keys()
    if missing:
        players.update(_players(missing))

    prices = Prices(season, now)
    grants = _unpaid_grants(roster)
    trades = roster.trades_available + sum(
        grant.trades for grant in grants if grant.granted_at <= now
    )
    grants = [grant for grant in grants if grant.granted_at > now]
    upcoming_grants = list(grants)

    cash = roster.cash
    start_value = cash + sum((players[pk].current_salary or ZERO for pk in squad_ids), ZERO)
    result = PlanResult(
        plan=plan,
        now=now,
        days=[],
        cash_start=cash,
        trades_start=trades,
        squad_start=list(squad_ids),
        team_value_start=start_value,
    )

    made = _Made(roster, min((step.created_at for step in steps if step.created_at), default=None))
    start_gaps = set(position_shortfalls(_counts(squad_ids, players)))
    squad = list(squad_ids)

    by_day = {}
    for step in steps:
        by_day.setdefault(step.processing_date, []).append(step)

    for date in sorted(by_day):
        moment = processing_moment(date)
        is_past = moment <= now
        day = DayResult(
            date=date,
            moment=moment,
            price_source=prices.source(moment),
            is_past=is_past,
        )
        arriving = [grant for grant in grants if grant.granted_at < moment]
        grants = [grant for grant in grants if grant.granted_at >= moment]
        day.grants = arriving
        trades += sum(grant.trades for grant in arriving)

        day.squad_before = list(squad)
        day.cash_before = cash
        day.trades_before = trades

        on_roster = set(squad)
        seen = set()
        for step in by_day[date]:
            outcome = StepResult(step=step)
            day.steps.append(outcome)
            # Saved steps only: a preview step has no history to be done in.
            if step.created_at and made.claim(step):
                outcome.status = DONE
                continue
            if is_past:
                outcome.status = MISSED
                outcome.reason = _("This day has passed and the move was never made.")
                continue

            if step.kind == PlanStep.Kind.TRADE:
                problem = _season_problem(season, date) or _trade_problem(
                    step, players, on_roster, seen
                )
                if problem:
                    outcome.status = STALE
                    outcome.reason = problem
                    continue
                out, in_ = players[step.player_out_id], players[step.player_in_id]
                outcome.price_out = prices.price(out, day.price_source)
                outcome.price_in = prices.price(in_, day.price_source)
                if outcome.price_out is None or outcome.price_in is None:
                    outcome.status = STALE
                    outcome.reason = _("No salary on record for %(player)s.") % {
                        "player": out if outcome.price_out is None else in_
                    }
                    continue
                seen.update((out.pk, in_.pk))
                outcome.cash_delta = outcome.price_out - outcome.price_in
                trades -= 1
                result.trades_used += 1
                squad[squad.index(out.pk)] = in_.pk
            else:
                problem = _market_problem(season, moment)
                if problem:
                    outcome.status = STALE
                    outcome.reason = problem
                    continue
                if step.kind == PlanStep.Kind.BUY_TRADE:
                    outcome.cash_delta = -TRADE_BUY_PRICE
                    trades += 1
                    result.trades_bought += 1
                else:
                    outcome.cash_delta = TRADE_SELL_PRICE
                    trades -= 1
                    result.trades_sold += 1
            cash += outcome.cash_delta

        day.squad_after = list(squad)
        day.cash_after = cash
        day.trades_after = trades
        day.counts_after = _counts(squad, players)
        if not is_past:
            _check_day(day, start_gaps)
        result.days.append(day)

    last_source = result.days[-1].price_source if result.days else KNOWN
    result.squad_end = list(squad)
    result.cash_end = cash
    result.trades_end = trades
    result.team_value_end = cash + sum(
        (prices.price(players[pk], last_source) or ZERO for pk in squad), ZERO
    )
    result.players = players
    result.grants = upcoming_grants
    return result


def _season_problem(season, date):
    if date < season.starts_on:
        return _("Trades only start once the season is under way.")
    if date > season.ends_on:
        return _("The season is over by then.")
    return ""


def _counts(squad, players):
    counts = dict.fromkeys(Player.Position.values, 0)
    for pk in squad:
        position = players[pk].position
        counts[position] = counts.get(position, 0) + 1
    return counts


def _trade_problem(step, players, on_roster, seen):
    """Why this trade cannot be made on its day, or "" when it can."""
    out, in_ = players.get(step.player_out_id), players.get(step.player_in_id)
    if out is None or in_ is None:
        return _("A player in this trade no longer exists.")
    if out.pk in seen or in_.pk in seen:
        return _("A player can only be in one move per processing day.")
    if out.pk not in on_roster:
        return _("%(player)s will not be on the roster that day.") % {"player": out}
    if in_.pk in on_roster:
        return _("%(player)s will already be on the roster that day.") % {"player": in_}
    return ""


def _market_problem(season, moment):
    """Why a trade cannot be bought or sold for that day's batch, or ""."""
    if season.trade_market_opens_at and moment <= season.trade_market_opens_at:
        return _("Trades can only be bought and sold once the trade market opens.")
    if season.trade_market_closes_at and moment > season.trade_market_closes_at:
        return _("The trade market has closed by then.")
    return ""


def _check_day(day, start_gaps):
    """The batch rule: the day as a whole must leave the roster legal."""
    if day.cash_after < 0:
        day.problems.append(
            _("Cash would end the day at %(cash)s.") % {"cash": millions(day.cash_after)}
        )
    if day.trades_after < 0:
        day.trades_short = -day.trades_after
        day.problems.append(
            ngettext(
                "%(count)d trade short.",
                "%(count)d trades short.",
                day.trades_short,
            )
            % {"count": day.trades_short}
        )
    gaps = [gap for gap in position_shortfalls(day.counts_after) if gap not in start_gaps]
    if gaps:
        day.problems.append(_("Positions short afterwards: %(gaps)s.") % {"gaps": ", ".join(gaps)})
