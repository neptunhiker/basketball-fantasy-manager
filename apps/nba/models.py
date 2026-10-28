"""Real-world basketball facts: who exists and which team they play for.

Nothing in here knows about salaries, caps or rosters. These rows describe the
NBA and would still be true if this app did not exist, which is what keeps them
separate from the fantasy game layer.
"""

import datetime as dt
import uuid
from decimal import Decimal
from itertools import pairwise

from django.conf import settings
from django.core.validators import MaxValueValidator, RegexValidator
from django.db import models
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel
from apps.nba.stats import HOTNESS_GAMES

# Injury report statuses that mean the player can play, compared casefolded.
HEALTHY_INJURY_STATUSES = frozenset({"healthy", "available", "returned", "cleared"})
# Hurt, but likely to play soon -- often the next game. Most of the feed is
# this, so it is told apart from a player who is out rather than lumped in.
DAY_TO_DAY_INJURY_STATUSES = frozenset({"day-to-day"})

HEALTHY = "healthy"
DAY_TO_DAY = "day_to_day"
INJURED = "injured"

# The game's minimum salary, in millions: no player is ever priced below it,
# so no expected salary is either.
MIN_SALARY = Decimal("0.50")


def injury_state(status):
    """`HEALTHY`, `DAY_TO_DAY` or `INJURED` for an injury report's status.

    No report at all (`None`) is healthy: the feed only lists the hurt.
    """
    status = (status or "").casefold()
    if not status or status in HEALTHY_INJURY_STATUSES:
        return HEALTHY
    if status in DAY_TO_DAY_INJURY_STATUSES:
        return DAY_TO_DAY
    return INJURED


def predict_salary(fantasy_points_per_game: float | Decimal) -> Decimal:
    """Return the expected salary in millions for a fantasy scoring rate.

    Never below `MIN_SALARY`: the polynomial dips under it for low scorers.
    """
    if isinstance(fantasy_points_per_game, Decimal):
        x = fantasy_points_per_game
    else:
        x = Decimal(str(fantasy_points_per_game))

    if x < 0:
        return MIN_SALARY

    salary = (
        Decimal("8.753479e-6") * x**4
        + Decimal("-8.583448e-4") * x**3
        + Decimal("3.058140e-2") * x**2
        + Decimal("-5.762906e-2") * x
        + Decimal("4.668975e-1")
    )

    return max(MIN_SALARY, salary).quantize(Decimal("0.01"))


def latest_fp_change(snapshots):
    """What a player scored between his two newest snapshots.

    `snapshots` is newest first; only the first two are read. Returns None
    unless both exist and belong to the same season -- a new season's first
    snapshot starts from zero and is not a game's worth of points. Otherwise a
    dict with `fp` (points gained), `fp_per_game` (how far the average moved),
    `as_of` and `previous_as_of`.

    No game in between leaves `fp` and `fp_per_game` None rather than 0: a rest
    day is not a player who took the floor and scored nothing, and the screens
    tell the two apart. The database twin is `apps.nba.views.with_latest_fp_change`.
    """
    if len(snapshots) < 2 or snapshots[0].season_id != snapshots[1].season_id:
        return None
    newer, older = snapshots[0], snapshots[1]
    played = newer.games_played > older.games_played
    averaged = played and newer.fp_per_game is not None and older.fp_per_game is not None
    return {
        "fp": newer.total_fp - older.total_fp if played else None,
        "fp_per_game": newer.fp_per_game - older.fp_per_game if averaged else None,
        "as_of": newer.as_of,
        "previous_as_of": older.as_of,
    }


class TeamManager(models.Manager):
    def get_by_natural_key(self, abbreviation):
        return self.get(abbreviation=abbreviation)


class Team(TimeStampedModel):
    """An NBA franchise."""

    class Conference(models.TextChoices):
        EAST = "EAST", _("Eastern Conference")
        WEST = "WEST", _("Western Conference")

    class Division(models.TextChoices):
        ATLANTIC = "ATLANTIC", _("Atlantic")
        CENTRAL = "CENTRAL", _("Central")
        SOUTHEAST = "SOUTHEAST", _("Southeast")
        NORTHWEST = "NORTHWEST", _("Northwest")
        PACIFIC = "PACIFIC", _("Pacific")
        SOUTHWEST = "SOUTHWEST", _("Southwest")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField("Name", max_length=64, unique=True)
    # Stable across seasons and already URL-safe, so this doubles as the key an
    # import matches on and the segment that appears in team URLs.
    abbreviation = models.CharField("Abbreviation", max_length=4, unique=True)
    conference = models.CharField("Conference", max_length=4, choices=Conference, blank=True)
    division = models.CharField("Division", max_length=16, choices=Division, blank=True)
    # The schedule feed's team ID -- ESPN's numbering, which never changes.
    espn_id = models.PositiveSmallIntegerField("ESPN ID", unique=True, null=True, blank=True)

    objects = TeamManager()

    class Meta:
        ordering = ["name"]
        verbose_name = "Team"
        verbose_name_plural = "Teams"

    def __str__(self):
        return self.name

    def natural_key(self):
        # Copying games from production to staging by `dumpdata
        # --natural-foreign`: every database gives its teams other UUIDs.
        return (self.abbreviation,)


class Player(TimeStampedModel):
    """An NBA player.

    Also the identity registry: every scraped stat line and every future salary
    snapshot points at one of these rows, so a trade or a corrected spelling is
    a single update instead of a rewrite of history.
    """

    class Position(models.TextChoices):
        GUARD = "G", _("Guard")
        FORWARD = "F", _("Forward")
        CENTER = "C", _("Center")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Derived from the name on first save and then left alone. A player URL that
    # has been shared or bookmarked should keep working even if we later fix the
    # spelling of their name, which is exactly why this is not the primary key.
    slug = models.SlugField("Slug", max_length=140, unique=True, blank=True)
    first_name = models.CharField("First name", max_length=64, blank=True)
    last_name = models.CharField("Last name", max_length=64)
    # The roster rules of the official game count guards, forwards and centers,
    # so this drives cap validation later and is required, not optional.
    position = models.CharField("Position", max_length=1, choices=Position)
    team = models.ForeignKey(
        Team,
        verbose_name="Team",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="players",
        help_text="Empty for free agents or players not currently on a roster.",
    )
    is_active = models.BooleanField(
        "Active",
        default=True,
        help_text="Cleared by the import when a player stops appearing in the source.",
    )
    # basketball.de's own number for the player, taken from their profile link.
    # Names drift (suffixes, accents, a corrected spelling) and teams change
    # with every trade, so after the first import this is the only key the
    # import matches on. Empty for players basketball.de has never listed.
    bbde_id = models.PositiveIntegerField(
        "basketball.de ID",
        unique=True,
        null=True,
        blank=True,
        help_text="Set by the import. Fill in by hand to link a player the import could not match.",
    )
    # RotoWire's number for the player, from their news feed. Stored on the
    # first match by name and team, so a later trade or a "Jr." on one side
    # does not lose the link. Empty until the player has been in the news.
    rotowire_id = models.PositiveIntegerField(
        "RotoWire ID",
        unique=True,
        null=True,
        blank=True,
        help_text="Set by the news import. Fill in by hand to link unmatched news.",
    )
    is_rookie = models.BooleanField(
        "Rookie",
        default=False,
        help_text="As flagged by the official game. Set by the import.",
    )
    # The four `current_*` columns are a cache of the newest PlayerSnapshot, not
    # authored values. The snapshot history remains the source of truth; these
    # columns exist so that every player row, affordability check, cap
    # calculation and team average is a plain column read instead of a
    # correlated subquery per player. All are maintained together by
    # apps.fantasy.services.sync_player_from_latest_snapshot, and all are NULL
    # until a snapshot exists -- which is not the same as zero, and no screen
    # may present it as such.
    current_salary = models.DecimalField(
        "Current salary",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Mirrored from the newest snapshot. Empty until there is one.",
    )
    current_total_fp = models.DecimalField(
        "Fantasy points",
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Points this season, from the newest snapshot.",
    )
    current_fp_per_game = models.DecimalField(
        "Points per game",
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Empty until the player has appeared in a game.",
    )
    # Kept alongside the two point columns because a team's scoring rate is
    # total points over total games -- an average of per-game averages weights
    # someone who played twice like someone who played twenty times.
    current_games_played = models.PositiveSmallIntegerField(
        "Games played",
        null=True,
        blank=True,
        help_text="Games played, from the newest snapshot.",
    )

    class Meta:
        ordering = ["last_name", "first_name"]
        verbose_name = "Player"
        verbose_name_plural = "Players"
        indexes = [
            models.Index(fields=["last_name", "first_name"]),
            models.Index(fields=["position"]),
            models.Index(fields=["current_salary"]),
            # "Who scores most per game" is the ordering the value screens will
            # all want, and the team detail page already sorts on it.
            models.Index(fields=["current_fp_per_game"]),
        ]

    def __str__(self):
        return self.full_name

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def predicted_salary(self):
        if self.current_fp_per_game is None:
            return None
        return predict_salary(self.current_fp_per_game)

    @property
    def latest_injury(self):
        injuries = getattr(self, "injury_history", None)
        if injuries is None:
            return self.injuries.order_by("-observed_at", "-created_at").first()
        return injuries[0] if injuries else None

    @property
    def injury_state(self):
        latest = self.latest_injury
        return injury_state(latest.status if latest else None)

    @property
    def is_injured(self):
        """Out, or anything else short of playing -- not day-to-day."""
        return self.injury_state == INJURED

    @property
    def is_day_to_day(self):
        return self.injury_state == DAY_TO_DAY

    @property
    def expected_return(self):
        """The date the latest report expects the player back, while hurt.

        A healthy player has nothing to return from, so a date left on an old
        report never leaks through once they are marked healthy again.
        """
        if self.injury_state == HEALTHY:
            return None
        return self.latest_injury.return_date

    @property
    def return_in_days(self):
        """Days until `expected_return`; negative once that date has passed."""
        if self.expected_return is None:
            return None
        return (self.expected_return - timezone.localdate()).days

    def hotness_score(self):
        """Return how often the player's FP/game rose after his recent games.

        Snapshots are written daily, but a player plays only every other day or
        so: a day without a game leaves the average untouched and must not count
        as a miss. So only snapshots where `games_played` went up are kept, and
        the newest `HOTNESS_GAMES` + 1 of those give up to `HOTNESS_GAMES`
        comparisons. Several games between two snapshots (a missed download) are
        one comparison -- all we know is where the average ended up. Only the
        most recent season is read, so cumulative stats from different seasons
        are never compared.
        """
        prefetched = getattr(self, "_prefetched_objects_cache", {}).get("snapshots")
        if prefetched is not None:
            snapshots = sorted(prefetched, key=lambda snapshot: snapshot.as_of, reverse=True)
            latest = snapshots[0] if snapshots else None
        else:
            snapshot_query = self.snapshots.order_by("-as_of")
            latest = snapshot_query.first()
            snapshots = snapshot_query.filter(season_id=latest.season_id) if latest else []

        if latest is None:
            return None

        snapshots = [
            snapshot
            for snapshot in reversed(snapshots)
            if snapshot.season_id == latest.season_id and snapshot.fp_per_game is not None
        ]

        # Oldest to newest: keep a snapshot only once more games have been
        # played. Rest days drop out, and so does a source correcting the count
        # downwards.
        games = []
        for snapshot in snapshots:
            if not games or snapshot.games_played > games[-1].games_played:
                games.append(snapshot)
        games = games[-(HOTNESS_GAMES + 1) :]

        increases = 0
        comparisons = 0
        for previous, current in pairwise(games):
            comparisons += 1
            if current.fp_per_game > previous.fp_per_game:
                increases += 1

        if comparisons == 0:
            return None
        return f"{increases}/{comparisons}"

    def latest_fp_change(self):
        """What moved between his two newest snapshots; see `latest_fp_change`.

        Reads the prefetched snapshots when a list brought them along, so a
        roster of fifteen costs no query per row.
        """
        prefetched = getattr(self, "_prefetched_objects_cache", {}).get("snapshots")
        if prefetched is not None:
            snapshots = sorted(prefetched, key=lambda snapshot: snapshot.as_of, reverse=True)[:2]
        else:
            snapshots = list(self.snapshots.order_by("-as_of")[:2])
        return latest_fp_change(snapshots)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._build_slug()
        super().save(*args, **kwargs)

    def _build_slug(self):
        """A readable slug, disambiguated only when two players really collide.

        Rare, but real: the league has fielded more than one Chris Johnson. The
        unique constraint is still the authority here -- this loop only keeps the
        common case tidy.
        """
        base = slugify(self.full_name) or "player"
        slug = base
        suffix = 2
        while Player.objects.filter(slug=slug).exclude(pk=self.pk).exists():
            slug = f"{base}-{suffix}"
            suffix += 1
        return slug


class PlayerNote(TimeStampedModel):
    """A private note attached to a player by one account."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        "accounts.User",
        verbose_name="User",
        on_delete=models.CASCADE,
        related_name="player_notes",
    )
    player = models.ForeignKey(
        Player,
        verbose_name="Player",
        on_delete=models.CASCADE,
        related_name="notes",
    )
    content = models.TextField("Note", max_length=2000)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Player note"
        verbose_name_plural = "Player notes"

    @property
    def was_edited(self):
        """True once the note was changed after it was written (not on save noise)."""
        return self.updated_at - self.created_at > dt.timedelta(minutes=1)

    def __str__(self):
        return f"{self.user} · {self.player}: {self.content[:40]}"


class TeamNote(TimeStampedModel):
    """A private note attached to a team by one account."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        "accounts.User",
        verbose_name="User",
        on_delete=models.CASCADE,
        related_name="team_notes",
    )
    team = models.ForeignKey(
        "Team",
        verbose_name="Team",
        on_delete=models.CASCADE,
        related_name="notes",
    )
    content = models.TextField("Note", max_length=2000)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Team note"
        verbose_name_plural = "Team notes"

    @property
    def was_edited(self):
        """True once the note was changed after it was written (not on save noise)."""
        return self.updated_at - self.created_at > dt.timedelta(minutes=1)

    def __str__(self):
        return f"{self.user} · {self.team}: {self.content[:40]}"


class PlayerInjury(TimeStampedModel):
    """An injury observation received from an external NBA data provider."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    player = models.ForeignKey(
        Player,
        verbose_name="Player",
        on_delete=models.CASCADE,
        related_name="injuries",
    )
    provider = models.CharField("Provider", max_length=64, default="api-basketball-nba")
    provider_injury_id = models.CharField("Provider injury ID", max_length=64, blank=True)
    provider_source_id = models.CharField("Provider source ID", max_length=64, blank=True)
    provider_source_state = models.CharField("Provider source state", max_length=32, blank=True)
    provider_source_description = models.CharField(
        "Provider source description", max_length=128, blank=True
    )
    observed_at = models.DateTimeField("Observed at")
    reported_at = models.DateTimeField("Reported at", null=True, blank=True)
    status = models.CharField("Status", max_length=64)
    injury_status_type = models.CharField("Injury status type", max_length=128, blank=True)
    fantasy_status = models.CharField("Fantasy status", max_length=64, blank=True)
    fantasy_status_abbreviation = models.CharField(
        "Fantasy status abbreviation", max_length=16, blank=True
    )
    return_date = models.DateField("Expected return date", null=True, blank=True)
    injury_type = models.CharField("Injury type", max_length=128, blank=True)
    injury_location = models.CharField("Injury location", max_length=128, blank=True)
    injury_detail = models.CharField("Injury detail", max_length=128, blank=True)
    injury_side = models.CharField("Injury side", max_length=64, blank=True)
    short_comment = models.TextField("Short comment", blank=True)
    long_comment = models.TextField("Long comment", blank=True)
    headline = models.TextField("Headline", blank=True)
    headline_source = models.CharField("Headline source", max_length=128, blank=True)
    feed_player_name = models.CharField("Feed player name", max_length=160, blank=True)
    feed_athlete_id = models.CharField("Feed athlete ID", max_length=64, blank=True)
    feed_player_position = models.CharField("Feed player position", max_length=64, blank=True)
    feed_player_position_abbreviation = models.CharField(
        "Feed player position abbreviation", max_length=8, blank=True
    )
    feed_player_status = models.CharField("Feed player status", max_length=64, blank=True)
    feed_team_id = models.CharField("Feed team ID", max_length=64, blank=True)
    feed_team_abbreviation = models.CharField("Feed team abbreviation", max_length=8, blank=True)
    feed_team_name = models.CharField("Feed team name", max_length=128, blank=True)
    raw_payload = models.JSONField("Raw provider payload", default=dict, blank=True)

    class Meta:
        ordering = ["-observed_at", "-created_at"]
        verbose_name = "Player injury"
        verbose_name_plural = "Player injuries"
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "provider_injury_id", "observed_at"],
                condition=~models.Q(provider_injury_id=""),
                name="unique_provider_injury_observation",
            ),
        ]
        indexes = [
            models.Index(fields=["player", "-observed_at"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"{self.player} - {self.status}"


class NbaApiUsage(TimeStampedModel):
    """Daily outbound-request counter for the NBA provider."""

    provider = models.CharField("Provider", max_length=64)
    usage_date = models.DateField("Usage date")
    request_count = models.PositiveSmallIntegerField("Request count", default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "usage_date"], name="one_api_usage_row_per_day"
            ),
        ]

    def __str__(self):
        return f"{self.provider} - {self.usage_date}: {self.request_count}"


class ApiQuota(TimeStampedModel):
    """What a RapidAPI provider last reported about the monthly requests left.

    RapidAPI counts every request made with the key, from staging and
    production alike, so this is the one tally both servers agree on.
    """

    provider = models.CharField("Provider", max_length=64, unique=True)
    limit = models.PositiveIntegerField("Monthly limit", null=True, blank=True)
    remaining = models.PositiveIntegerField("Remaining")
    resets_at = models.DateTimeField("Resets at", null=True, blank=True)
    reported_at = models.DateTimeField("Reported at")

    class Meta:
        verbose_name = "API quota"

    def __str__(self):
        return f"{self.provider}: {self.remaining} of {self.limit} left"


# ESPN's team IDs, used by the schedule feed, by our abbreviation.
ESPN_TEAM_IDS = {
    "ATL": 1,
    "BOS": 2,
    "NOP": 3,
    "CHI": 4,
    "CLE": 5,
    "DAL": 6,
    "DEN": 7,
    "DET": 8,
    "GSW": 9,
    "HOU": 10,
    "IND": 11,
    "LAC": 12,
    "LAL": 13,
    "MIA": 14,
    "MIL": 15,
    "MIN": 16,
    "BKN": 17,
    "NYK": 18,
    "ORL": 19,
    "PHI": 20,
    "PHX": 21,
    "POR": 22,
    "SAC": 23,
    "SAS": 24,
    "OKC": 25,
    "UTA": 26,
    "WAS": 27,
    "TOR": 28,
    "MEM": 29,
    "CHA": 30,
}


# RotoWire's team codes where they are not ours. PHO is in the feed; the rest
# are the other spellings the same family of feeds is known to use.
ROTOWIRE_TEAM_CODES = {
    "PHO": "PHX",
    "BRK": "BKN",
    "CHO": "CHA",
    "GS": "GSW",
    "NY": "NYK",
    "NO": "NOP",
    "SA": "SAS",
}


class PlayerNews(TimeStampedModel):
    """One RotoWire news item about an NBA player.

    The feed only ever shows its newest 25 items, so every item is kept once
    seen. `rotowire_news_id` is what makes a second pull of the same items an
    update instead of a copy. `player` stays empty while nobody here matches;
    the `feed_*` columns keep what RotoWire said so a later import can retry.
    """

    class Kind(models.TextChoices):
        INJURY = "injury", _("Injury")
        NEWS = "news", _("News")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rotowire_news_id = models.PositiveIntegerField("RotoWire news ID", unique=True)
    player = models.ForeignKey(
        Player,
        verbose_name="Player",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="news",
    )
    kind = models.CharField("Kind", max_length=8, choices=Kind, default=Kind.NEWS)
    headline = models.CharField("Headline", max_length=255)
    body = models.TextField("Text", blank=True)
    url = models.URLField("Article", max_length=500)
    # The feed dates an item but does not time it. Within a day its IDs give
    # the order: RotoWire numbers its items as it publishes them.
    published_on = models.DateField("Published on")
    first_seen_at = models.DateTimeField("First seen at", default=timezone.now)
    feed_player_id = models.PositiveIntegerField("Feed player ID", null=True, blank=True)
    feed_player_name = models.CharField("Feed player name", max_length=160)
    feed_team_abbreviation = models.CharField("Feed team", max_length=8, blank=True)
    feed_position = models.CharField("Feed position", max_length=8, blank=True)
    raw_payload = models.JSONField("Raw provider payload", default=dict, blank=True)

    class Meta:
        ordering = ["-published_on", "-rotowire_news_id"]
        verbose_name = "Player news"
        verbose_name_plural = "Player news"
        indexes = [
            models.Index(fields=["player", "-published_on"]),
            models.Index(fields=["-published_on", "-rotowire_news_id"]),
            models.Index(fields=["kind"]),
        ]

    def __str__(self):
        return f"{self.feed_player_name}: {self.headline}"

    @property
    def is_injury(self):
        return self.kind == self.Kind.INJURY

    @property
    def team_code(self):
        """The team as we write it: "PHX" for the feed's "PHO"."""
        code = self.feed_team_abbreviation
        return ROTOWIRE_TEAM_CODES.get(code, code)


class ImportRun(TimeStampedModel):
    """One attempt to import the player list from basketball.de.

    The audit trail of the import: who started it, how far it got and what it
    could not place. Deliberately has nowhere to put the basketball.de login --
    the credentials live only in memory for the length of one run.
    """

    class Source(models.TextChoices):
        WEB = "web", _("Web")
        COMMAND = "command", _("Command")
        SCHEDULED = "scheduled", _("Scheduled")

    class Status(models.TextChoices):
        RUNNING = "running", _("Running")
        SUCCEEDED = "succeeded", _("Succeeded")
        FAILED = "failed", _("Failed")

    started_at = models.DateTimeField("Started at", default=timezone.now)
    finished_at = models.DateTimeField("Finished at", null=True, blank=True)
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Triggered by",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    source = models.CharField("Source", max_length=16, choices=Source)
    status = models.CharField("Status", max_length=16, choices=Status, default=Status.RUNNING)
    # A dry run does all the work and rolls it back, so the summary can be read
    # before the first real import touches anything.
    dry_run = models.BooleanField("Dry run", default=False)
    season = models.ForeignKey(
        "fantasy.Season",
        verbose_name="Season",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    pages = models.PositiveIntegerField("Pages", default=0)
    rows = models.PositiveIntegerField("Rows", default=0)
    matched = models.PositiveIntegerField("Matched", default=0)
    created = models.PositiveIntegerField("Created", default=0)
    reactivated = models.PositiveIntegerField("Reactivated", default=0)
    inactivated = models.PositiveIntegerField("Inactivated", default=0)
    # Short human-readable lines rather than structured data: these are read by
    # a person deciding what to fix by hand, never by code.
    created_players = models.JSONField("Created players", default=list, blank=True)
    ambiguous = models.JSONField("Ambiguous rows", default=list, blank=True)
    skipped = models.JSONField("Skipped rows", default=list, blank=True)
    warnings = models.JSONField("Warnings", default=list, blank=True)
    error = models.TextField("Error", blank=True)

    class Meta:
        ordering = ["-started_at"]
        verbose_name = "Import run"
        verbose_name_plural = "Import runs"

    def __str__(self):
        return f"{self.get_status_display()} import, {self.started_at:%Y-%m-%d %H:%M}"


# An NBA season as basketball people write it: the year it starts and the last
# two digits of the year it ends.
season_label_validator = RegexValidator(
    r"^\d{4}-\d{2}$", "Use the format YYYY-YY, for example 2025-26."
)


def _per_game(label):
    """A one-decimal per-game average, as the official stats print them."""
    return models.DecimalField(label, max_digits=4, decimal_places=1)


def _percentage(label):
    return models.DecimalField(
        label, max_digits=4, decimal_places=1, validators=[MaxValueValidator(100)]
    )


class SeasonStatsImport(TimeStampedModel):
    """One upload of an official season stats file.

    Holds the preview between upload and confirmation: the parsed, matched rows
    sit in `payload` until a superuser confirms or discards them, so the file
    is uploaded once and never kept. Afterwards the record stays as the audit
    trail of who changed which season's stats, and `payload` is emptied.
    """

    class Source(models.TextChoices):
        WEB = "web", _("Web")
        COMMAND = "command", _("Command")

    class Status(models.TextChoices):
        PREVIEWED = "previewed", _("Previewed")
        IMPORTED = "imported", _("Imported")
        DISCARDED = "discarded", _("Discarded")
        EXPIRED = "expired", _("Expired")
        FAILED = "failed", _("Failed")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    season = models.CharField("Season", max_length=7, validators=[season_label_validator])
    file_name = models.CharField("File name", max_length=255, blank=True)
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Triggered by",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    source = models.CharField("Source", max_length=16, choices=Source)
    status = models.CharField("Status", max_length=16, choices=Status, default=Status.PREVIEWED)
    rows = models.PositiveIntegerField("Rows", default=0)
    matched = models.PositiveIntegerField("Matched", default=0)
    created = models.PositiveIntegerField("Created", default=0)
    updated = models.PositiveIntegerField("Updated", default=0)
    removed = models.PositiveIntegerField("Removed", default=0)
    # Short human-readable lines, as on ImportRun: read by a person deciding
    # what to fix, never by code.
    unmatched = models.JSONField("Unmatched rows", default=list, blank=True)
    ambiguous = models.JSONField("Ambiguous rows", default=list, blank=True)
    skipped = models.JSONField("Skipped rows", default=list, blank=True)
    warnings = models.JSONField("Warnings", default=list, blank=True)
    # The matched rows between preview and confirmation, emptied once the
    # preview is imported, discarded or expired.
    payload = models.JSONField("Pending rows", default=list, blank=True)
    imported_at = models.DateTimeField("Imported at", null=True, blank=True)
    error = models.TextField("Error", blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Season stats import"
        verbose_name_plural = "Season stats imports"

    def __str__(self):
        return f"{self.season} {self.get_status_display()}, {self.created_at:%Y-%m-%d %H:%M}"


class PlayerSeasonStats(TimeStampedModel):
    """A player's official per-game averages for one finished NBA season.

    Reference data from an outside file, not something the game computes: the
    numbers are stored exactly as published, percentages included, because
    recalculating them from rounded averages would be wrong for players who
    rarely shoot. The team is the one the file names, as text -- it is last
    season's team, and many players have moved since.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    player = models.ForeignKey(
        Player,
        verbose_name="Player",
        on_delete=models.CASCADE,
        related_name="season_stats",
    )
    season = models.CharField("Season", max_length=7, validators=[season_label_validator])
    # The name as the file spelled it, to trace a row back to its source line.
    source_name = models.CharField("Name in file", max_length=128)
    import_run = models.ForeignKey(
        SeasonStatsImport,
        verbose_name="Import",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stats",
    )
    team_abbreviation = models.CharField("Team", max_length=4, blank=True)
    age = models.PositiveSmallIntegerField("Age")
    games_played = models.PositiveSmallIntegerField("Games played")
    wins = models.PositiveSmallIntegerField("Wins")
    losses = models.PositiveSmallIntegerField("Losses")
    minutes = _per_game("Minutes")
    points = _per_game("Points")
    field_goals_made = _per_game("Field goals made")
    field_goals_attempted = _per_game("Field goals attempted")
    field_goal_pct = _percentage("Field goal %")
    threes_made = _per_game("3-pointers made")
    threes_attempted = _per_game("3-pointers attempted")
    three_pct = _percentage("3-point %")
    free_throws_made = _per_game("Free throws made")
    free_throws_attempted = _per_game("Free throws attempted")
    free_throw_pct = _percentage("Free throw %")
    offensive_rebounds = _per_game("Offensive rebounds")
    defensive_rebounds = _per_game("Defensive rebounds")
    rebounds = _per_game("Rebounds")
    assists = _per_game("Assists")
    turnovers = _per_game("Turnovers")
    steals = _per_game("Steals")
    blocks = _per_game("Blocks")
    personal_fouls = _per_game("Personal fouls")
    # This game's fantasy points per game, worked out from the averages above
    # when the file is read: see apps.nba.season_stats.game_fantasy_points.
    # Two decimals because of the 0.5 factors; negative is possible.
    game_fantasy_points = models.DecimalField("Fantasy points", max_digits=5, decimal_places=2)

    class Meta:
        ordering = ["-season", "player__last_name", "player__first_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["player", "season"], name="one_stats_row_per_player_per_season"
            ),
        ]
        indexes = [models.Index(fields=["season", "game_fantasy_points"])]
        verbose_name = "Player season stats"
        verbose_name_plural = "Player season stats"

    def __str__(self):
        return f"{self.player} {self.season}"


# A regular season is 82 games a team. Two of them are only scheduled once the
# NBA Cup's group stage ends in December, so counting what is stored would
# come up short until then; what is left is worked out from this instead.
REGULAR_SEASON_GAMES = 82
# How long after tip-off a game counts as played. The schedule is loaded
# rarely and carries no results, so the clock decides.
GAME_LENGTH = dt.timedelta(hours=3)
# ESPN's status names for a game that will not be played when it was set for.
CALLED_OFF_STATUSES = frozenset({"STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_SUSPENDED"})


class GameQuerySet(models.QuerySet):
    def for_team(self, team):
        return self.filter(models.Q(home_team=team) | models.Q(away_team=team))

    def counting(self):
        """Regular-season games that are on: the ones the 82 are made of."""
        return self.filter(is_regular_season=True).exclude(status__in=CALLED_OFF_STATUSES)

    def played(self, now=None):
        return self.counting().filter(tip_off__lte=(now or timezone.now()) - GAME_LENGTH)

    def upcoming(self, now=None):
        return self.counting().filter(tip_off__gt=(now or timezone.now()) - GAME_LENGTH)


class Game(TimeStampedModel):
    """One NBA game on the schedule, as the schedule feed last had it.

    Loaded a few times a season and never with results: whether a game has
    been played is read off the clock (`GAME_LENGTH` after tip-off).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    espn_id = models.CharField("ESPN ID", max_length=20, unique=True)
    # The year the season ends in, as the feed counts: 2027 for 2026-27.
    season_year = models.PositiveSmallIntegerField("Season")
    tip_off = models.DateTimeField("Tip-off")
    home_team = models.ForeignKey(
        Team, on_delete=models.PROTECT, related_name="home_games", verbose_name="Home team"
    )
    away_team = models.ForeignKey(
        Team, on_delete=models.PROTECT, related_name="away_games", verbose_name="Away team"
    )
    # ESPN's own status name: STATUS_SCHEDULED, STATUS_FINAL, STATUS_POSTPONED...
    status = models.CharField("Status", max_length=40, blank=True)
    note = models.CharField("Note", max_length=200, blank=True)
    is_regular_season = models.BooleanField("Regular season", default=True)

    objects = GameQuerySet.as_manager()

    class Meta:
        ordering = ["tip_off", "espn_id"]
        indexes = [
            models.Index(fields=["home_team", "tip_off"]),
            models.Index(fields=["away_team", "tip_off"]),
            models.Index(fields=["season_year", "tip_off"]),
        ]

    def __str__(self):
        matchup = f"{self.away_team.abbreviation} @ {self.home_team.abbreviation}"
        return f"{matchup}, {timezone.localtime(self.tip_off):%Y-%m-%d}"

    @property
    def is_called_off(self):
        return self.status in CALLED_OFF_STATUSES

    @property
    def is_nba_cup(self):
        return "NBA Cup" in self.note

    def is_played(self, now=None):
        return not self.is_called_off and self.tip_off <= (now or timezone.now()) - GAME_LENGTH

    def opponent_of(self, team):
        return self.opponent_of_id(team.pk)

    def opponent_of_id(self, team_id):
        return self.away_team if self.home_team_id == team_id else self.home_team

    def is_home_for(self, team):
        return self.home_team_id == team.pk
