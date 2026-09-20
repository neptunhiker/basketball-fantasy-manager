"""The game layer: seasons, rosters, and the transactions that change them.

`apps.nba` describes the real league. This app describes playing the game on
top of it, so everything here may import from `nba` and nothing in `nba` knows
this app exists.

Three pieces work together and are easy to confuse:

- `RosterPlayer` answers "who is on this roster", the most common question in
  the app, so it is a real table rather than something replayed from a log.
- `Transaction` answers "how did it get that way".
- `Roster.cash` is a balance that only transactions move.

Keeping membership and the log in sync is therefore the job of
`apps.fantasy.services`, not of anything that writes these models directly.

`Manager` sits between the login and the roster, which is why it lives here
rather than in `accounts`: a manager exists because rosters do, and `accounts`
is an auth app that knows nothing about basketball.
"""

import datetime as dt
import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from django.db import models
from django.db.models import F, Prefetch, Q
from django.db.models.functions import NullIf
from django.utils import timezone
from django.utils.functional import cached_property

from apps.core.models import TimeStampedModel
from apps.nba.models import Player, PlayerInjury, Team

# The official game's rules. Kept together so the day they change, they change
# in one place.
ROSTER_SIZE = 15
MINIMUM_BY_POSITION = {
    Player.Position.GUARD: 5,
    Player.Position.FORWARD: 5,
    Player.Position.CENTER: 2,
}
STARTING_CASH = Decimal("60000000")
STARTING_TRADES = 2
TRADE_BUY_PRICE = Decimal("1500000")
TRADE_SELL_PRICE = Decimal("1000000")
ROSTER_ICON_CHOICES = [
    ("bear", "Bear"),
    ("bunny", "Bunny"),
    ("croc", "Crocodile"),
    ("deer", "Deer"),
    ("elephant", "Elephant"),
    ("fox", "Fox"),
    ("fox_2", "Fox 2"),
    ("giraffe", "Giraffe"),
    ("koala", "Koala"),
    ("lion", "Lion"),
    ("monkey", "Monkey"),
    ("owl", "Owl"),
    ("panda", "Panda"),
    ("racoon", "Racoon"),
    ("seal", "Seal"),
    ("sonic", "Sonic"),
    ("squirrel", "Squirrel"),
    ("tiger", "Tiger"),
    ("wolf", "Wolf"),
    ("zebra", "Zebra"),
]


def position_shortfalls(counts):
    """Which position minimums `counts` falls short of, phrased for the screen.

    A free function rather than a method on Roster, because the trade screen has
    to ask the question of a roster that does not exist yet: the counts it
    *would* have after a swap. Empty when every minimum is met.
    """
    gaps = []
    for position, minimum in MINIMUM_BY_POSITION.items():
        if (short := minimum - counts[position]) > 0:
            label = Player.Position(position).label
            gaps.append(f"{short} more {label}{'s' if short > 1 else ''}")
    return gaps


class Season(TimeStampedModel):
    """One season of the official game, e.g. 2025-26."""

    class Timing(models.TextChoices):
        UPCOMING = "UPCOMING", "Upcoming"
        RUNNING = "RUNNING", "In progress"
        FINISHED = "FINISHED", "Finished"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    label = models.CharField("Label", max_length=16, unique=True)
    starts_on = models.DateField("Starts on")
    ends_on = models.DateField("Ends on")
    # Salaries recalculate weekly and stats accrue per season, so almost every
    # future query needs to know which season it is looking at.
    is_current = models.BooleanField("Current", default=False)
    signings_open_at = models.DateTimeField(
        "Signings open",
        null=True,
        blank=True,
        help_text="Before this moment rosters cannot be created and players cannot be signed. Leave empty to open immediately.",
    )
    signings_close_at = models.DateTimeField(
        "Signings close",
        null=True,
        blank=True,
        help_text=(
            "After this moment a roster can only change through trades: no more "
            "buying, no more selling, and no new rosters. Leave empty to keep "
            "signings open for the whole season."
        ),
    )

    class Meta:
        ordering = ["-starts_on"]
        verbose_name = "Season"
        verbose_name_plural = "Seasons"
        constraints = [
            models.UniqueConstraint(
                fields=["is_current"],
                condition=Q(is_current=True),
                name="only_one_current_season",
            ),
            models.CheckConstraint(
                condition=Q(ends_on__gt=models.F("starts_on")),
                name="season_ends_after_it_starts",
            ),
        ]

    def __str__(self):
        return self.label

    @property
    def signings_open(self):
        """Backward-compatible alias for the transaction permission."""
        return self.transactions_allowed

    @property
    def season_open_at(self):
        return timezone.make_aware(dt.datetime.combine(self.starts_on, dt.time.min))

    @property
    def season_close_at(self):
        return timezone.make_aware(dt.datetime.combine(self.ends_on, dt.time.max))

    @property
    def transactions_allowed(self):
        """Whether buying and releasing are allowed right now."""
        now = timezone.now()
        if now > self.season_close_at:
            return False
        if self.signings_open_at is None and self.signings_close_at is None:
            return True
        if self.signings_open_at and now < self.signings_open_at:
            return False
        if self.signings_close_at and now >= self.signings_close_at:
            return False
        return True

    @property
    def trading_allowed(self):
        """Whether swapping one roster player for another is allowed."""
        if self.signings_open_at is None and self.signings_close_at is None:
            return True
        now = timezone.now()
        return self.season_open_at <= now <= self.season_close_at

    def clean(self):
        """Validate the lifecycle dates and their required ordering."""
        super().clean()
        if self.starts_on and self.ends_on and self.ends_on <= self.starts_on:
            raise ValidationError({"ends_on": _("A season has to end after it starts.")})
        if not self.starts_on or not self.ends_on:
            return
        season_open = timezone.make_aware(dt.datetime.combine(self.starts_on, dt.time.min))
        season_close = timezone.make_aware(dt.datetime.combine(self.ends_on, dt.time.max))
        if self.signings_open_at and self.signings_close_at:
            if self.signings_open_at >= self.signings_close_at:
                raise ValidationError({"signings_close_at": _("Signings must close after they open.")})
            if self.signings_close_at >= season_open:
                raise ValidationError({"signings_close_at": _("Signings must close before the season starts.")})
        if self.signings_open_at and self.signings_open_at >= season_open:
            raise ValidationError({"signings_open_at": _("Signings must open before the season starts.")})
        if self.signings_close_at and self.signings_close_at > season_close:
            raise ValidationError({"signings_close_at": _("Signings cannot close after the season ends.")})

    @property
    def timing(self):
        """Where today falls relative to this season.

        Distinct from `is_current`, which is a choice we make about which season
        the app works with. A season can be flagged current for weeks before it
        actually tips off -- as 2026-27 is right now.
        """
        today = timezone.localdate()
        if today < self.starts_on:
            return self.Timing.UPCOMING
        if today > self.ends_on:
            return self.Timing.FINISHED
        return self.Timing.RUNNING


class Manager(TimeStampedModel):
    """The person behind a roster: who is playing, and under what name.

    Distinct from `User`, which is the login. A `User` holds the facts about a
    person -- their name, their password, whether they may sign in. A `Manager`
    is a way of playing: rosters hang off it, and it carries the handle those
    rosters are played under.

    Separate because the two do not have to be one to one. A user may keep
    several profiles, and does not become a manager until they build something
    to manage. What a manager cannot be is anonymous -- `user` is not nullable,
    so every manager traces back to an account.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="User",
        on_delete=models.CASCADE,
        # Not `managers`: `User._meta.managers` already means Django's queryset
        # managers, and `user.managers` would read like those.
        related_name="manager_profiles",
    )
    # The in-game handle, which is what the official game shows beside a team.
    # A person's real name stays on `User`, where it describes the person and
    # not the way they are playing.
    nick_name = models.CharField("Nickname", max_length=40)

    class Meta:
        ordering = ["nick_name"]
        verbose_name = "Manager"
        verbose_name_plural = "Managers"
        constraints = [
            models.UniqueConstraint(fields=["user", "nick_name"], name="unique_nickname_per_user"),
        ]

    def __str__(self):
        return self.nick_name

    @property
    def display_name(self):
        return self.nick_name or self.user.display_name


class WatchlistEntry(TimeStampedModel):
    """A private, persistent player watch entry owned by one account."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="User",
        on_delete=models.CASCADE,
        related_name="watchlist_entries",
    )
    player = models.ForeignKey(
        Player,
        verbose_name="Player",
        on_delete=models.PROTECT,
        related_name="watchlist_entries",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Watchlist entry"
        verbose_name_plural = "Watchlist entries"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "player"],
                name="one_watchlist_entry_per_user_player",
            ),
        ]

    def __str__(self):
        return f"{self.user} · {self.player}"


class RosterQuerySet(models.QuerySet):
    def with_squad(self):
        return self.prefetch_related(
            Prefetch(
                "memberships",
                queryset=RosterPlayer.objects.as_squad(),
                to_attr="open_memberships",
            )
        )


class Roster(TimeStampedModel):
    """A team a manager has put together, or a what-if they are considering."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    manager = models.ForeignKey(
        Manager,
        verbose_name="Manager",
        on_delete=models.CASCADE,
        related_name="rosters",
    )
    season = models.ForeignKey(
        Season, verbose_name="Season", on_delete=models.PROTECT, related_name="rosters"
    )
    name = models.CharField("Name", max_length=80)
    icon = models.CharField(
        "Icon",
        max_length=32,
        choices=ROSTER_ICON_CHOICES,
        default="koala",
    )
    # A balance, not a derivation. Weekly price changes must not retroactively
    # alter what a past purchase cost, and the salary cap enforces itself here:
    # you cannot buy what you cannot afford.
    cash = models.DecimalField("Cash", max_digits=12, decimal_places=2, default=STARTING_CASH)
    trades_available = models.PositiveIntegerField(
        "Available trades", default=STARTING_TRADES
    )

    class Meta:
        ordering = ["name"]
        verbose_name = "Roster"
        verbose_name_plural = "Rosters"
        constraints = [
            models.UniqueConstraint(
                fields=["manager", "season", "name"],
                name="unique_roster_name_per_manager_and_season",
            ),
            models.CheckConstraint(condition=Q(cash__gte=0), name="cash_is_never_negative"),
            models.CheckConstraint(
                condition=Q(trades_available__gte=0), name="trades_available_is_never_negative"
            ),
        ]

    objects = RosterQuerySet.as_manager()

    def __str__(self):
        return self.name

    @property
    def current_players(self):
        """The players on the roster right now, as a queryset.

        For callers that want to filter or join further. Anything that just
        wants to read the squad should use `current_squad`, which is loaded
        once per roster instead of once per question asked of it.
        """
        return Player.objects.filter(
            roster_memberships__roster=self, roster_memberships__removed_at__isnull=True
        )

    @cached_property
    def current_memberships(self):
        """The open spells, ordered as the screens show them.

        The single load behind every derived figure on this model, and behind
        the build page's squad list. One query per roster instance, and none at
        all when the caller came through `Roster.objects.with_squad()`.

        Cached because the build page asks the same question a dozen times over
        -- the size, the value, the shortfalls, and `is_complete` once per row
        of the market it renders below. Each of those used to be its own query.
        A caller that changes the roster invalidates this by reloading it; see
        `refresh_from_db`.
        """
        if (prefetched := getattr(self, "open_memberships", None)) is not None:
            return prefetched
        return list(self.memberships.as_squad())

    @cached_property
    def current_squad(self):
        """The players themselves, in the same order as their spells."""
        return [membership.player for membership in self.current_memberships]

    def refresh_from_db(self, *args, **kwargs):
        """Reload, and drop the cached squad along with the fields.

        This is the invalidation point for `current_memberships`: a reload is
        the caller saying the roster may have moved under them, which is
        exactly when a squad counted a moment ago stops being true. Every
        writer in `services` already reloads the caller's instance after a buy,
        a sell or a trade, so a page that renders after a change renders from
        the new squad rather than the old one.

        Django clears its own prefetch cache here but knows nothing about a
        `to_attr` list or a `cached_property`, so both are cleared by hand.
        """
        super().refresh_from_db(*args, **kwargs)
        for cached in ("current_memberships", "current_squad", "open_memberships"):
            self.__dict__.pop(cached, None)

    def position_counts(self):
        """How many of each position are on the roster right now.

        Seeded with every `Player.Position` so a position nobody fills still
        reports 0 rather than being absent. Counted with `get` rather than
        `counts[position] += 1` because `position` is a one-character
        CharField and `choices` is not enforced by the database: a row that
        carries an unknown code is a data problem, and raising KeyError from
        here would turn it into a 500 on the build page instead.
        """
        counts = dict.fromkeys(Player.Position.values, 0)
        for player in self.current_squad:
            counts[player.position] = counts.get(player.position, 0) + 1
        return counts

    @property
    def player_count(self):
        return len(self.current_squad)

    def missing(self):
        """What still stands between this roster and a legal one, for the screen.

        Returns an empty list when the roster is complete. The salary cap is
        deliberately absent: it is enforced by `cash` never going below zero,
        so a roster that exists is already within budget.
        """
        counts = self.position_counts()
        gaps = position_shortfalls(counts)

        if (short := ROSTER_SIZE - sum(counts.values())) > 0:
            gaps.append(f"{short} more players")
        elif short < 0:
            gaps.append(f"{-short} players too many")

        return gaps

    @property
    def is_complete(self):
        return not self.missing()

    def position_progress(self):
        """View-model for the build screen's counters.

        Built from the same MINIMUM_BY_POSITION the completeness check reads, so
        the numbers on screen cannot drift from the numbers being enforced.
        """
        counts = self.position_counts()
        return [
            {
                "label": Player.Position(position).label,
                "code": position,
                "count": counts[position],
                "minimum": minimum,
                "short": max(0, minimum - counts[position]),
            }
            for position, minimum in MINIMUM_BY_POSITION.items()
        ]

    @property
    def squad_value(self):
        """What the current squad would cost at today's prices.

        Not the same as what was paid for it: prices move weekly, and `cash` is
        the record of the spending.
        """
        # Counted here rather than by the database, because the squad is
        # already loaded and a price that is not on record is not a price of
        # zero -- `SUM` skipped those rows, and so does this.
        return sum(
            (player.current_salary for player in self.current_squad if player.current_salary),
            Decimal("0"),
        )


class RosterPlayerQuerySet(models.QuerySet):
    def open(self):
        """The spells that have not ended: who is on the roster right now.

        Named for the shape of the row rather than for the question, because
        the same filter answers several -- who is on this roster, whether this
        player is already signed, what the squad is worth.
        """
        return self.filter(removed_at__isnull=True)

    def as_squad(self):
        """The open spells as a squad: joined and ordered the way screens read it.

        One definition rather than one per caller. The build page's team card,
        the trade modal's outgoing list and the counters on the roster list are
        all this same list, and the ordering is what puts each position's rows
        together with the most expensive player first.
        """
        return (
            self.open()
            .select_related("player", "player__team")
            .prefetch_related(
                Prefetch("player__snapshots", queryset=PlayerSnapshot.objects.order_by("-as_of")),
                Prefetch(
                    "player__injuries",
                    queryset=PlayerInjury.objects.order_by("-observed_at", "-created_at"),
                    to_attr="injury_history",
                ),
            )
            .order_by("player__position", "-player__current_salary")
        )


class RosterPlayer(TimeStampedModel):
    """One player's spell on one roster.

    A player who is sold and later bought back gets a second row rather than
    having the first one reopened, so the history reads as what actually
    happened.
    """

    roster = models.ForeignKey(
        Roster, verbose_name="Roster", on_delete=models.CASCADE, related_name="memberships"
    )
    player = models.ForeignKey(
        Player,
        verbose_name="Player",
        on_delete=models.PROTECT,
        related_name="roster_memberships",
    )
    added_at = models.DateTimeField("Added")
    removed_at = models.DateTimeField("Removed", null=True, blank=True)

    objects = RosterPlayerQuerySet.as_manager()

    class Meta:
        ordering = ["-added_at"]
        verbose_name = "Roster spell"
        verbose_name_plural = "Roster spells"
        constraints = [
            # Only the open spell is unique: the same player may appear twice in
            # the history of a roster, but never twice on it at once.
            models.UniqueConstraint(
                fields=["roster", "player"],
                condition=Q(removed_at__isnull=True),
                name="a_player_is_on_a_roster_once_at_a_time",
            ),
        ]

    def __str__(self):
        return f"{self.player} @ {self.roster}"

    @property
    def is_current(self):
        return self.removed_at is None


class Transaction(TimeStampedModel):
    """A single move: buying, selling, swapping players, or buying/selling trades."""

    class Kind(models.TextChoices):
        BUY = "BUY", "Buy"
        SELL = "SELL", "Sell"
        TRADE = "TRADE", "Trade"
        BUY_TRADE = "BUY_TRADE", "Buy Trade"
        SELL_TRADE = "SELL_TRADE", "Sell Trade"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    roster = models.ForeignKey(
        Roster, verbose_name="Roster", on_delete=models.CASCADE, related_name="transactions"
    )
    occurred_at = models.DateTimeField("Occurred at")
    kind = models.CharField(
        "Kind", max_length=20, choices=Kind.choices, blank=True
    )
    player_in = models.ForeignKey(
        Player,
        verbose_name="Player in",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="transactions_in",
    )
    player_out = models.ForeignKey(
        Player,
        verbose_name="Player out",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="transactions_out",
    )
    # What each side was worth at the moment of the move: a buy has only an
    # incoming price, a sale only an outgoing one, a trade has both. It is the
    # trade that needs them -- its `cash_delta` is the net of two figures, so
    # without these the two valuations cannot be recovered afterwards, and
    # `services.amount_paid_for` has to guess at a player's cost basis.
    #
    # Nullable because "unknown" is a real state rather than a gap to be filled
    # in: rows written before these columns existed hold only the net, and no
    # arithmetic can split it back into two prices.
    price_in = models.DecimalField(
        "Price in", max_digits=12, decimal_places=2, null=True, blank=True
    )
    price_out = models.DecimalField(
        "Price out", max_digits=12, decimal_places=2, null=True, blank=True
    )
    # Signed: negative when the roster spends, positive when it receives. Still
    # stored rather than derived from the prices above, because this is the money
    # that actually moved -- it is what `Roster.cash` reconciles against, and it
    # has to stay readable on a row whose prices were never recorded.
    cash_delta = models.DecimalField("Cash flow", max_digits=12, decimal_places=2)
    note = models.CharField("Note", max_length=200, blank=True)

    class Meta:
        ordering = ["-occurred_at"]
        verbose_name = "Transaction"
        verbose_name_plural = "Transactions"
        constraints = [
            models.CheckConstraint(
                condition=Q(player_in__isnull=False)
                | Q(player_out__isnull=False)
                | Q(kind__in=["BUY_TRADE", "SELL_TRADE"]),
                name="a_transaction_moves_at_least_one_player_or_trade",
            ),
            # NULL passes a CHECK in Postgres, which is exactly right here: an
            # unrecorded price is unknown, not negative.
            models.CheckConstraint(
                condition=Q(price_in__gte=0) & Q(price_out__gte=0),
                name="prices_are_never_negative",
            ),
            # The other direction is always a bug, legacy row or not: a price
            # for a side that moved no player means nothing.
            models.CheckConstraint(
                condition=(
                    (Q(price_in__isnull=True) | Q(player_in__isnull=False))
                    & (Q(price_out__isnull=True) | Q(player_out__isnull=False))
                ),
                name="a_price_needs_the_player_it_paid_for",
            ),
        ]

    def __str__(self):
        return f"{self.get_kind_display()}: {self.description}"

    @property
    def description(self):
        if self.kind == self.Kind.TRADE:
            return f"{self.player_out} → {self.player_in}"
        if self.kind in (self.Kind.BUY_TRADE, self.Kind.SELL_TRADE):
            return self.get_kind_display()
        return str(self.player_in or self.player_out)

    @property
    def is_priced(self):
        """Whether every side that moved a player also recorded its price.

        False on the rows written before those columns existed, which is why
        nothing may *require* the prices -- only check them when they are there.
        """
        return (self.price_in is not None or not self.player_in_id) and (
            self.price_out is not None or not self.player_out_id
        )

    @property
    def implied_cash_delta(self):
        """The money this move should have taken, read from the prices alone."""
        return (self.price_out or Decimal("0")) - (self.price_in or Decimal("0"))

    def save(self, *args, **kwargs):
        if not self.kind:
            if self.player_in_id and self.player_out_id:
                self.kind = self.Kind.TRADE
            elif self.player_in_id:
                self.kind = self.Kind.BUY
            elif self.player_out_id:
                self.kind = self.Kind.SELL
        super().save(*args, **kwargs)

    def clean(self):
        if not self.kind:
            if self.player_in_id and self.player_out_id:
                self.kind = self.Kind.TRADE
            elif self.player_in_id:
                self.kind = self.Kind.BUY
            elif self.player_out_id:
                self.kind = self.Kind.SELL

        if self.kind in (self.Kind.BUY, self.Kind.SELL, self.Kind.TRADE):
            if not (self.player_in_id or self.player_out_id):
                raise ValidationError(_("A transaction has to move at least one player."))
            if self.player_in_id and self.player_in_id == self.player_out_id:
                raise ValidationError(_("The player in and the player out cannot be the same."))
            # Two records of the same money, so they are worth comparing. Only when
            # both prices are known: an old row has nothing to disagree with.
            if self.is_priced and self.implied_cash_delta != self.cash_delta:
                out_str = f"{self.price_out:,.0f}" if self.price_out is not None else "0"
                in_str = f"{self.price_in:,.0f}" if self.price_in is not None else "0"
                raise ValidationError(
                    f"Cash flow {self.cash_delta:,.0f} does not match the prices: "
                    f"out ({out_str}) minus in ({in_str}) "
                    f"is {self.implied_cash_delta:,.0f}."
                )


class PlayerSnapshotQuerySet(models.QuerySet):
    def latest_per_player(self):
        """One row per player: the most recent one.

        Uses Postgres DISTINCT ON, so this is a single index scan rather than a
        subquery per player.
        """
        return self.order_by("player_id", "-as_of").distinct("player_id")

    def as_of(self, moment):
        """The state of the world as the source reported it at `moment`."""
        return self.filter(as_of__lte=moment).latest_per_player()


class PlayerSnapshot(TimeStampedModel):
    """What the official game said about one player at one moment.

    Written nightly, never edited. Salary, fantasy points and games played all
    move on their own schedules, and every question worth asking of this app --
    who got cheaper this week, whose form is improving, what did I pay -- is a
    comparison between two of these rows.

    `team` and `position` are recorded here as the source reported them *that
    night*, which is what makes a mid-season trade visible in hindsight. The
    current values still live on `Player`; see
    `services.sync_player_from_latest_snapshot` for how the two stay agreed.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    player = models.ForeignKey(
        Player, verbose_name="Player", on_delete=models.CASCADE, related_name="snapshots"
    )
    season = models.ForeignKey(
        Season, verbose_name="Season", on_delete=models.CASCADE, related_name="snapshots"
    )
    # When these values were current in the game, not when we scraped them. A
    # scrape-time column would let a re-parse or a backfill rewrite history.
    as_of = models.DateTimeField("As of")

    salary = models.DecimalField("Salary", max_digits=12, decimal_places=2)
    total_fp = models.DecimalField("Fantasy points", max_digits=8, decimal_places=2)
    games_played = models.PositiveSmallIntegerField("Games played")
    # Computed by Postgres and stored, so it can be ordered and filtered on
    # without being a second number that can disagree with the first two.
    # NULL rather than an error before the first game is played.
    fp_per_game = models.GeneratedField(
        verbose_name="Points per game",
        expression=F("total_fp") / NullIf(F("games_played"), 0),
        output_field=models.DecimalField(max_digits=8, decimal_places=2),
        db_persist=True,
    )

    team = models.ForeignKey(
        Team,
        verbose_name="Team",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="player_snapshots",
        help_text="The team the source reported for this player at that moment.",
    )
    position = models.CharField("Position", max_length=1, choices=Player.Position)

    objects = PlayerSnapshotQuerySet.as_manager()

    class Meta:
        ordering = ["-as_of", "player"]
        verbose_name = "Player snapshot"
        verbose_name_plural = "Player snapshots"
        constraints = [
            models.UniqueConstraint(
                fields=["player", "season", "as_of"],
                name="one_snapshot_per_player_and_moment",
            ),
        ]
        indexes = [
            # Serves both "this player's history" and the DISTINCT ON above.
            models.Index(fields=["player", "-as_of"]),
            models.Index(fields=["season", "-as_of"]),
        ]

    def __str__(self):
        return f"{self.player} @ {self.as_of:%Y-%m-%d}"
