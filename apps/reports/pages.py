"""What the Reports page draws: a run, its briefing, and a viewer's rosters.

A read model over stored runs. Player refs in the AI's answers become players,
shortlist ids become trade cards drawn from our own numbers (never from the
AI's text), and a recommended trade gets its track record once the snapshots
have moved past it. A handful of queries per page, however many rosters.
"""

import datetime as dt
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Count, Max, Q
from django.utils import timezone

from apps.fantasy.models import PlayerSnapshot, Transaction
from apps.fantasy.points import PointsHistory
from apps.nba.models import Player

from .models import RecommendedTrade, Report, ReportRun

# How long a recommendation is followed for its track record: the first week,
# and the three weeks the advice is meant to look ahead.
TRACK_DAYS = (7, 21)


@dataclass
class BriefingItem:
    kind: str
    text: str
    players: list


@dataclass
class TradeCard:
    rank: int
    ref: str
    out: Player
    into: Player
    salary_out: Decimal
    salary_in: Decimal
    cash_after: Decimal
    fp_gain: int
    projected_out: int | None
    projected_in: int | None
    change_out: Decimal | None
    change_in: Decimal | None
    confidence: str
    reasoning: str
    # Empty on reports from before these were asked for.
    horizon: str = ""
    why_it_lasts: str = ""
    what_ends_it: str = ""
    # Why the incoming player counted as rising ("form", "opening"), or None.
    riser: str | None = None
    # The gain if both players keep their recent form, None on older reports.
    fp_gain_recent: int | None = None

    @property
    def horizon_label(self):
        return RecommendedTrade.Horizon(self.horizon).label if self.horizon else ""

    @property
    def riser_kinds(self):
        return set((self.riser or "").split("+")) - {""}

    @property
    def shows_recent_gain(self):
        """Worth its own figure: when recent form tells a different story."""
        return self.fp_gain_recent is not None and (
            self.riser or abs(self.fp_gain_recent - self.fp_gain) >= 50
        )

    @property
    def salary_swing(self):
        """Predicted salary gain of the swap at the next update: in minus out."""
        if self.change_in is None and self.change_out is None:
            return None
        return (self.change_in or 0) - (self.change_out or 0)


@dataclass
class Window:
    """Points the newcomer scored minus points the departing player scored,
    over the first `days` days after the advice (`elapsed` of them so far)."""

    days: int
    elapsed: int
    gained: Decimal | None
    ends_on: dt.date

    @property
    def complete(self):
        return self.gained is not None and self.elapsed >= self.days


@dataclass
class Outcome:
    """How a recommended trade has done since, over each of `TRACK_DAYS`."""

    trade: RecommendedTrade
    windows: list
    followed: bool

    @property
    def first(self):
        return self.windows[0]

    @property
    def gained(self):
        return self.first.gained

    @property
    def days(self):
        return self.first.elapsed

    @property
    def pending(self):
        return self.gained is None

    @property
    def later(self):
        """The longer windows, once the first week is over."""
        return self.windows[1:] if self.first.complete else []


@dataclass
class RosterSection:
    roster: object
    report: Report
    trades: list = field(default_factory=list)
    watch: list = field(default_factory=list)
    outcomes: list = field(default_factory=list)
    previous_run: ReportRun | None = None
    previous: list = field(default_factory=list)

    @property
    def output(self):
        return self.report.output or {}

    @property
    def decision(self):
        return self.output.get("decision")


def published_runs(season):
    """The season's published runs, newest first, with their roster counts."""
    return list(
        ReportRun.objects.filter(season=season, is_published=True)
        .annotate(roster_count=Count("reports", filter=Q(reports__kind=Report.Kind.ROSTER)))
        .order_by("-created_at")
    )


def can_see(run, user):
    return run.is_published or user.is_staff


def page(run, user, roster_id=None, now=None):
    """Everything the run's page shows to `user`.

    Players see the sections of their own rosters. Staff also get every other
    roster in the run (`roster_choices`) and see the one asked for by
    `roster_id`, their own otherwise.
    """
    now = now or timezone.now()
    reports = list(
        run.reports.select_related("roster__manager__user").prefetch_related(
            "trades__player_out", "trades__player_in"
        )
    )
    briefing = next((report for report in reports if report.is_briefing), None)
    roster_reports = sorted(
        (report for report in reports if not report.is_briefing and report.roster),
        key=lambda report: (report.roster.name.lower(), str(report.roster.pk)),
    )
    own = [report for report in roster_reports if report.roster.manager.user_id == user.pk]
    shown = own
    if user.is_staff and roster_id:
        shown = [report for report in roster_reports if str(report.roster.pk) == str(roster_id)]
    elif user.is_staff and not own:
        shown = roster_reports[:1]

    previous_run, previous_reports = _previous(run, [report.roster for report in shown])
    players = _players(run, briefing, shown, previous_reports)
    newest = _newest_snapshot(run.season)
    history = _history(run, shown, previous_run, previous_reports, newest)

    sections = []
    for report in shown:
        section = RosterSection(roster=report.roster, report=report)
        if report.status == Report.Status.SUCCEEDED:
            section.trades = _trade_cards(report, players)
            section.watch = [
                (players.get(_ref_id(run, item["player_ref"])), item["text"])
                for item in section.output.get("watch", [])
            ]
            section.outcomes = _outcomes(report, run, newest, history)
        before = previous_reports.get(report.roster.pk)
        if before is not None:
            section.previous_run = previous_run
            section.previous = _outcomes(before, previous_run, newest, history)
        sections.append(section)

    return {
        "run": run,
        "briefing": briefing,
        "briefing_items": _briefing_items(run, briefing, players),
        "sections": sections,
        "roster_choices": [report.roster for report in roster_reports] if user.is_staff else [],
        "shown_roster_ids": {str(report.roster.pk) for report in shown},
        "has_other_rosters": bool(roster_reports) and not own,
    }


# --- pieces ---------------------------------------------------------------------


def _ref_id(run, ref):
    player_id = run.player_refs.get(ref)
    return uuid.UUID(player_id) if player_id else None


def _players(run, briefing, reports, previous_reports):
    """Every player the page names, in one query."""
    ids = set()
    if briefing and briefing.output:
        for item in briefing.output.get("items", []):
            ids |= {_ref_id(run, ref) for ref in item.get("player_refs", [])}
    for report in reports:
        for item in (report.output or {}).get("watch", []):
            ids.add(_ref_id(run, item["player_ref"]))
        for trade in report.shortlist:
            ids |= {uuid.UUID(trade["out"]), uuid.UUID(trade["in"])}
    for report in previous_reports.values():
        for trade in report.trades.all():
            ids |= {trade.player_out_id, trade.player_in_id}
    ids.discard(None)
    return Player.objects.select_related("team").in_bulk(ids)


def _briefing_items(run, briefing, players):
    if briefing is None or not briefing.output:
        return []
    items = []
    for item in briefing.output.get("items", []):
        named = [players.get(_ref_id(run, ref)) for ref in item.get("player_refs", [])]
        items.append(
            BriefingItem(kind=item["kind"], text=item["text"], players=[p for p in named if p])
        )
    return items


def _trade_cards(report, players):
    by_ref = {trade["ref"]: trade for trade in report.shortlist}
    cards = []
    for pick in report.output.get("trades", []):
        data = by_ref.get(pick["trade_id"])
        if data is None:
            continue
        out, into = players.get(uuid.UUID(data["out"])), players.get(uuid.UUID(data["in"]))
        if out is None or into is None:
            continue
        cards.append(
            TradeCard(
                rank=pick["priority"],
                ref=pick["trade_id"],
                out=out,
                into=into,
                salary_out=Decimal(data["salary_out"]),
                salary_in=Decimal(data["salary_in"]),
                cash_after=Decimal(data["cash_after"]),
                fp_gain=data["fp_gain"],
                projected_out=data.get("projected_out"),
                projected_in=data.get("projected_in"),
                change_out=_decimal(data.get("change_out")),
                change_in=_decimal(data.get("change_in")),
                confidence=pick["confidence"],
                reasoning=pick["reasoning"],
                horizon=pick.get("horizon", ""),
                why_it_lasts=pick.get("why_it_lasts", ""),
                what_ends_it=pick.get("what_ends_it", ""),
                riser=data.get("riser"),
                fp_gain_recent=data.get("fp_gain_recent"),
            )
        )
    return cards


def _decimal(value):
    return None if value is None else Decimal(value)


def _previous(run, rosters):
    """The published run before this one, and its reports for these rosters."""
    previous = (
        ReportRun.objects.filter(
            season=run.season, is_published=True, created_at__lt=run.created_at
        )
        .order_by("-created_at")
        .first()
    )
    if previous is None or not rosters:
        return previous, {}
    reports = previous.reports.filter(
        kind=Report.Kind.ROSTER,
        status=Report.Status.SUCCEEDED,
        roster__in=rosters,
    ).prefetch_related("trades__player_out__team", "trades__player_in__team")
    return previous, {report.roster_id: report for report in reports}


def _newest_snapshot(season):
    return PlayerSnapshot.objects.filter(season=season).aggregate(moment=Max("as_of"))["moment"]


@dataclass
class _History:
    points: object
    made: set


def _history(run, reports, previous_run, previous_reports, newest):
    """The points history and the trades actually made, for every outcome on
    the page, in two queries."""
    trades = [trade for report in reports for trade in report.trades.all()]
    trades += [trade for report in previous_reports.values() for trade in report.trades.all()]
    if not trades or newest is None:
        return _History(points=None, made=set())
    player_ids = {trade.player_out_id for trade in trades} | {
        trade.player_in_id for trade in trades
    }
    earliest = min(r.created_at for r in [run, previous_run] if r is not None)
    made = set(
        Transaction.objects.filter(
            kind=Transaction.Kind.TRADE,
            roster_id__in={report.roster_id for report in reports} | set(previous_reports),
            occurred_at__gte=earliest,
        ).values_list("roster_id", "player_out_id", "player_in_id", "occurred_at")
    )
    return _History(points=PointsHistory(run.season, player_ids), made=made)


def _outcomes(report, run, newest, history):
    trades = list(report.trades.all())
    if not trades:
        return []
    start = run.data_as_of
    outcomes = []
    for trade in trades:
        followed = any(
            roster_id == report.roster_id
            and out_id == trade.player_out_id
            and in_id == trade.player_in_id
            and occurred_at >= run.created_at
            for roster_id, out_id, in_id, occurred_at in history.made
        )
        outcomes.append(
            Outcome(
                trade=trade,
                windows=[_window(trade, start, days, newest, history) for days in TRACK_DAYS],
                followed=followed,
            )
        )
    return outcomes


def _window(trade, start, days, newest, history):
    full = start + dt.timedelta(days=days)
    end = min(newest, full) if newest else start
    gained = None
    if history.points is not None and end > start:
        scored_in = history.points.spell_points(trade.player_in_id, start, end)
        scored_out = history.points.spell_points(trade.player_out_id, start, end)
        if scored_in is not None and scored_out is not None:
            gained = scored_in - scored_out
    return Window(
        days=days,
        elapsed=max((end - start).days, 0),
        gained=gained,
        ends_on=timezone.localdate(full),
    )
