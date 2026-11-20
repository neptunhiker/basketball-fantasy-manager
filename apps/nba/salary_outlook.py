"""Where a player's salary is heading at the next Sunday update.

The official game reprices every player on Sundays at 22:00, from the opening
of trade buying on (`Season.salary_update_moments`). Its formula is not
published; this app reads a salary as two parts:

    salary = expected salary for his points per game + pick premium

The expected salary is the curve of `apps.nba.models.predict_salary`: what
his output alone is worth. The pick premium is the rest -- how many managers
bought or sold him. Nobody publishes picks, so the premium is read off the last
update, as the part of that salary the curve does not explain, and carried
over in full:

    premium   = salary - expected salary, both from the first snapshot after the last update
    predicted = expected salary for his points per game now + premium
    change    = predicted - current salary

Salaries only move at an update, so the change comes down to how far his
points per game have moved along the curve since then; the premium sets the
level, and shows who is priced above his output.

The game only reprices a player who has played `MIN_GAMES_FOR_UPDATE` games
by the update; short of that he keeps his starting salary. So:

- fewer games than that now: no change predicted, he keeps today's salary;
- fewer at the last update, enough now: his first repricing is coming. His
  salary then was his starting price, not a price picks moved, so there is no
  premium to read (None) and none is carried: predicted = expected salary;
- enough at both: the premium as above.

Before a season's first update there is no premium to read: pre-season prices
are set by the game, not by picks. It counts as zero then, and the baseline is
the last snapshot before the season starts.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from django.db.models import (
    Case,
    DecimalField,
    ExpressionWrapper,
    F,
    IntegerField,
    OuterRef,
    Q,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import Coalesce, Greatest
from django.utils import timezone

from apps.fantasy.models import PlayerSnapshot, Season

from .models import (
    MILLION,
    MIN_GAMES_FOR_UPDATE,
    MIN_SALARY,
    expected_salary_expression,
    predict_salary,
    repriced_at_update,
)

AFTER_UPDATE = "update"
PRESEASON = "preseason"

DOLLARS = DecimalField(max_digits=20, decimal_places=2)
POINTS = DecimalField(max_digits=8, decimal_places=2)


@dataclass(frozen=True)
class Baseline:
    """What every player's next salary is predicted from.

    `kind` is `AFTER_UPDATE` -- each player's first snapshot at or after
    `moment`, the last salary update -- or `PRESEASON`, his last snapshot
    before `moment`, the season's start. `next_update` is when the prediction
    comes due, None once the season has no update left.
    """

    season: Season
    kind: str
    moment: datetime
    next_update: datetime | None

    @property
    def is_preseason(self):
        return self.kind == PRESEASON


def salary_baseline(season=None, now=None):
    """The baseline for `season` (default: the current one), or None.

    None without a season, and once its last update is behind it: there is
    nothing left to predict. A season without a trade buying date yet has no
    known updates, and keeps its pre-season baseline until it gets one.

    An update only counts once a snapshot has been read after it: between
    22:00 and the import at 22:15 the salaries on record are still the old
    ones, and so is the baseline they are compared with.
    """
    season = season or Season.objects.filter(is_current=True).first()
    if season is None:
        return None
    now = now or timezone.now()
    updates = season.salary_update_moments()
    next_update = next((moment for moment in updates if moment > now), None)
    if updates and next_update is None:
        return None
    newest_read = (
        PlayerSnapshot.objects.filter(season=season, as_of__lte=now)
        .order_by("-as_of")
        .values_list("as_of", flat=True)
        .first()
    )
    read = [moment for moment in updates if newest_read and moment <= newest_read]
    if read:
        return Baseline(season, AFTER_UPDATE, read[-1], next_update)
    return Baseline(season, PRESEASON, season.season_open_at, next_update)


def salary_outlook_context(baseline):
    """What a page needs for the next-update figures and their note."""
    return {
        "salary_baseline": baseline,
        # Every premium is zero before the first update: a column of zeros
        # says nothing, so it only appears once there is one to read.
        "show_premium": baseline is not None and not baseline.is_preseason,
    }


def baseline_snapshot(snapshots, baseline):
    """The player's snapshot the baseline points at, from all of his snapshots."""
    own = [snapshot for snapshot in snapshots if snapshot.season_id == baseline.season.pk]
    if baseline.is_preseason:
        before = [snapshot for snapshot in own if snapshot.as_of < baseline.moment]
        return max(before, key=lambda snapshot: snapshot.as_of, default=None)
    after = [snapshot for snapshot in own if snapshot.as_of >= baseline.moment]
    return min(after, key=lambda snapshot: snapshot.as_of, default=None)


def expected_amount(fp_per_game):
    """`predict_salary` in dollars, None without points per game."""
    if fp_per_game is None:
        return None
    return predict_salary(fp_per_game) * MILLION


def pick_premium(snapshot, preseason=False):
    """How far a snapshot's salary sits above (or below) its expected salary.

    Zero for a pre-season price, which no pick has moved yet. None for a
    player the update skipped -- fewer than `MIN_GAMES_FOR_UPDATE` games, so
    his salary is still his starting price and says nothing about picks.
    """
    if preseason:
        return Decimal("0")
    if not repriced_at_update(snapshot.games_played):
        return None
    expected = expected_amount(snapshot.fp_per_game)
    if expected is None:
        return None
    return snapshot.salary - expected


def predicted_salary(fp_per_game, premium):
    """The expected salary for `fp_per_game` plus the premium, never below the minimum."""
    expected = expected_amount(fp_per_game)
    if expected is None or premium is None:
        return None
    return max(MIN_SALARY * MILLION, expected + premium)


@dataclass(frozen=True)
class SalaryOutlook:
    """One player's prediction for the next update, in dollars.

    `frozen`: too few games for the update to reprice him, so `salary` is
    today's and `change` zero. `premium` is None when the last update skipped
    him (see `pick_premium`); `expected` is None without points per game.
    `games_played` is his count now.
    """

    baseline: PlayerSnapshot
    premium: Decimal | None
    expected: Decimal | None
    salary: Decimal
    change: Decimal
    games_played: int | None = None
    frozen: bool = False

    games_needed = MIN_GAMES_FOR_UPDATE

    @property
    def first_repricing(self):
        """Skipped at the last update, enough games for the next one."""
        return not self.frozen and self.premium is None

    @property
    def games_to_go(self):
        """Games he still needs before an update reprices him; 0 once he has them."""
        return max(0, MIN_GAMES_FOR_UPDATE - (self.games_played or 0))


def salary_outlook(player, baseline, snapshots=None):
    """The player's `SalaryOutlook`, or None when a figure it needs is missing.

    Reads the prefetched snapshots when the caller has them, so a list of
    players costs no query per row.
    """
    if baseline is None or player.current_salary is None:
        return None
    if snapshots is None:
        prefetched = getattr(player, "_prefetched_objects_cache", {}).get("snapshots")
        snapshots = prefetched if prefetched is not None else player.snapshots.all()
    snapshot = baseline_snapshot(snapshots, baseline)
    if snapshot is None:
        return None
    premium = pick_premium(snapshot, baseline.is_preseason)
    expected = expected_amount(player.current_fp_per_game)
    if not repriced_at_update(player.current_games_played):
        return SalaryOutlook(
            baseline=snapshot,
            premium=premium,
            expected=expected,
            salary=player.current_salary,
            change=Decimal("0"),
            frozen=True,
            games_played=player.current_games_played,
        )
    # No premium means the last update skipped him: his salary is still his
    # starting price, with no picks in it to carry over.
    carried = premium if premium is not None else Decimal("0")
    salary = predicted_salary(player.current_fp_per_game, carried)
    if salary is None:
        return None
    return SalaryOutlook(
        baseline=snapshot,
        premium=premium,
        expected=expected,
        salary=salary,
        change=salary - player.current_salary,
        games_played=player.current_games_played,
    )


def with_salary_outlook(qs, baseline):
    """Annotate `pick_premium`, `outlook_salary` and `outlook_change` (dollars or None).

    The database twin of `salary_outlook`, so lists can sort by the change.
    """
    if baseline is None:
        return qs.annotate(
            pick_premium=Value(None, output_field=DOLLARS),
            outlook_salary=Value(None, output_field=DOLLARS),
            outlook_change=Value(None, output_field=DOLLARS),
        )
    snapshots = PlayerSnapshot.objects.filter(player_id=OuterRef("pk"), season=baseline.season)
    if baseline.is_preseason:
        snapshot = snapshots.filter(as_of__lt=baseline.moment).order_by("-as_of")[:1]
    else:
        snapshot = snapshots.filter(as_of__gte=baseline.moment).order_by("as_of")[:1]
    qs = qs.annotate(
        baseline_salary=Subquery(snapshot.values("salary"), output_field=DOLLARS),
        baseline_fp_per_game=Subquery(snapshot.values("fp_per_game"), output_field=POINTS),
        baseline_games=Subquery(snapshot.values("games_played"), output_field=IntegerField()),
    )
    if baseline.is_preseason:
        premium = Case(
            When(baseline_salary__isnull=False, then=Value(Decimal("0"))), output_field=DOLLARS
        )
    else:
        # Only an update that repriced him has picks in it to read.
        premium = Case(
            When(
                baseline_games__gte=MIN_GAMES_FOR_UPDATE,
                then=F("baseline_salary") - expected_salary_expression("baseline_fp_per_game"),
            ),
            output_field=DOLLARS,
        )
    qs = qs.annotate(pick_premium=premium)
    # Nothing to say without his salary at the baseline and today.
    known = Q(baseline_salary__isnull=False, current_salary__isnull=False)
    frozen = Q(current_games_played__isnull=True) | Q(current_games_played__lt=MIN_GAMES_FOR_UPDATE)
    qs = qs.annotate(
        outlook_salary=Case(
            # Too few games for the update to reprice him: he keeps today's salary.
            When(known & frozen, then=F("current_salary")),
            # GREATEST skips NULLs in Postgres, so the points per game have to
            # be ruled in first -- or he would be "predicted" $0.5M. No premium
            # (the last update skipped him) carries nothing.
            When(
                known & Q(current_fp_per_game__isnull=False),
                then=Greatest(
                    Value(MIN_SALARY * MILLION),
                    expected_salary_expression("current_fp_per_game")
                    + Coalesce(F("pick_premium"), Value(Decimal("0"))),
                ),
            ),
            output_field=DOLLARS,
        )
    )
    return qs.annotate(
        outlook_change=ExpressionWrapper(
            F("outlook_salary") - F("current_salary"), output_field=DOLLARS
        )
    )


def attach_salary_outlook(players, baseline):
    """Set the names `with_salary_outlook` annotates on players already loaded,
    so the shared templates read either. Returns the outlooks by player id."""
    outlooks = {}
    for player in players:
        outlook = salary_outlook(player, baseline)
        outlooks[player.pk] = outlook
        player.pick_premium = outlook.premium if outlook else None
        player.outlook_salary = outlook.salary if outlook else None
        player.outlook_change = outlook.change if outlook else None
    return outlooks


def squad_outlook(players, baseline):
    """The squad's predicted change at the next update, summed over its players.

    `missing` counts the players without a prediction, who are left out of the
    sum -- and the screen says so, rather than reading them as unchanged.
    None without a baseline, or when nobody on the squad has a prediction.
    """
    if baseline is None:
        return None
    changes = [player.outlook_change for player in players]
    known = [change for change in changes if change is not None]
    if not known:
        return None
    return {"change": sum(known, Decimal("0")), "missing": len(changes) - len(known)}


@dataclass(frozen=True)
class PastUpdate:
    """One Sunday update as the player's snapshots saw it, in dollars.

    `before` is his last snapshot ahead of the update -- the points per game
    the prediction was made from -- and `after` his first one behind it.
    `predicted` is what this module would have said at `before`; None when it
    could not have said anything, and `error` with it.
    """

    moment: datetime
    before: PlayerSnapshot | None
    after: PlayerSnapshot
    predicted: Decimal | None
    premium: Decimal | None

    @property
    def skipped(self):
        """Too few games at this update for the game to reprice him."""
        return not repriced_at_update(self.after.games_played)

    @property
    def change(self):
        return self.after.salary - self.before.salary if self.before else None

    @property
    def error(self):
        return self.after.salary - self.predicted if self.predicted is not None else None


def past_updates(snapshots, baseline):
    """Every update up to the baseline's with a snapshot after it, newest first.

    Ends where `salary_baseline` does, so an update counts here exactly when
    the prediction above it has moved on from it. Each prediction is rebuilt
    the way it would have been made at the time: the premium out of the update
    before (none before the first), applied to the points per game of his last
    snapshot ahead of this one -- or, short of six games then, his salary kept.
    """
    if baseline is None or baseline.is_preseason:
        return []
    season = baseline.season
    own = sorted(
        (snapshot for snapshot in snapshots if snapshot.season_id == season.pk),
        key=lambda snapshot: snapshot.as_of,
    )
    rows = []
    previous = Baseline(season, PRESEASON, season.season_open_at, None)
    moments = season.salary_update_moments(until=baseline.moment)
    for index, moment in enumerate(moments):
        after = next((snapshot for snapshot in own if snapshot.as_of >= moment), None)
        if after is None:
            break
        # Nothing read that week: the next snapshot belongs to a later update.
        if index + 1 < len(moments) and after.as_of >= moments[index + 1]:
            continue
        before = next((snapshot for snapshot in reversed(own) if snapshot.as_of < moment), None)
        rows.append(
            PastUpdate(
                moment=moment,
                before=before,
                after=after,
                predicted=_predicted_at(before, baseline_snapshot(own, previous), previous),
                premium=pick_premium(after),
            )
        )
        previous = Baseline(season, AFTER_UPDATE, moment, None)
    return rows[::-1]


def _predicted_at(before, start, previous):
    """What `salary_outlook` would have said at `before`, from the baseline
    snapshot `start` of the `previous` update -- the same three cases."""
    if before is None or start is None:
        return None
    if not repriced_at_update(before.games_played):
        return before.salary
    premium = pick_premium(start, previous.is_preseason)
    carried = premium if premium is not None else Decimal("0")
    return predicted_salary(before.fp_per_game, carried)


def average_error(rows):
    """How far the predictions were off on average, either way, and over how
    many updates: `{"error": ..., "count": ...}`, or None without one.

    Updates that skipped him are left out: "he keeps his salary" is right by
    the game's rule, and would only flatter the curve and the premium.
    """
    errors = [abs(row.error) for row in rows if row.error is not None and not row.skipped]
    if not errors:
        return None
    return {"error": sum(errors, Decimal("0")) / len(errors), "count": len(errors)}
