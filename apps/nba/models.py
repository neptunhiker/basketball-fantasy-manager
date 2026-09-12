"""Real-world basketball facts: who exists and which team they play for.

Nothing in here knows about salaries, caps or rosters. These rows describe the
NBA and would still be true if this app did not exist, which is what keeps them
separate from the fantasy game layer.
"""

import uuid
from decimal import Decimal
from itertools import pairwise

from django.db import models
from django.utils.text import slugify

from apps.core.models import TimeStampedModel


def predict_salary(fantasy_points_per_game: float | Decimal) -> Decimal:
    """Return the expected salary in millions for a fantasy scoring rate."""
    if isinstance(fantasy_points_per_game, Decimal):
        x = fantasy_points_per_game
    else:
        x = Decimal(str(fantasy_points_per_game))

    if x < 0:
        return Decimal("0.00")

    salary = (
        Decimal("8.753479e-6") * x**4
        + Decimal("-8.583448e-4") * x**3
        + Decimal("3.058140e-2") * x**2
        + Decimal("-5.762906e-2") * x
        + Decimal("4.668975e-1")
    )

    return max(Decimal("0.00"), salary).quantize(Decimal("0.01"))


class Team(TimeStampedModel):
    """An NBA franchise."""

    class Conference(models.TextChoices):
        EAST = "EAST", "Eastern Conference"
        WEST = "WEST", "Western Conference"

    class Division(models.TextChoices):
        ATLANTIC = "ATLANTIC", "Atlantic"
        CENTRAL = "CENTRAL", "Central"
        SOUTHEAST = "SOUTHEAST", "Southeast"
        NORTHWEST = "NORTHWEST", "Northwest"
        PACIFIC = "PACIFIC", "Pacific"
        SOUTHWEST = "SOUTHWEST", "Southwest"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField("Name", max_length=64, unique=True)
    # Stable across seasons and already URL-safe, so this doubles as the key an
    # import matches on and the segment that appears in team URLs.
    abbreviation = models.CharField("Abbreviation", max_length=4, unique=True)
    conference = models.CharField("Conference", max_length=4, choices=Conference, blank=True)
    division = models.CharField("Division", max_length=16, choices=Division, blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Team"
        verbose_name_plural = "Teams"

    def __str__(self):
        return self.name


class Player(TimeStampedModel):
    """An NBA player.

    Also the identity registry: every scraped stat line and every future salary
    snapshot points at one of these rows, so a trade or a corrected spelling is
    a single update instead of a rewrite of history.
    """

    class Position(models.TextChoices):
        GUARD = "G", "Guard"
        FORWARD = "F", "Forward"
        CENTER = "C", "Center"

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
    def is_injured(self):
        latest = self.latest_injury
        return bool(
            latest
            and latest.status.casefold()
            not in {"healthy", "available", "returned", "cleared"}
        )

    def hotness_score(self):
        """Return how often the player's FP/game has improved recently.

        The newest eleven snapshots provide up to ten comparisons. Snapshots
        are selected and ordered by ``as_of`` within the most recent season so
        cumulative stats from different seasons are never compared.
        """
        prefetched = getattr(self, "_prefetched_objects_cache", {}).get("snapshots")
        if prefetched is not None:
            snapshots = sorted(prefetched, key=lambda snapshot: snapshot.as_of, reverse=True)
            latest = snapshots[0] if snapshots else None
        else:
            snapshot_query = self.snapshots.order_by("-as_of")
            latest = snapshot_query.first()
            snapshots = snapshot_query.filter(season_id=latest.season_id)[:11] if latest else []

        if latest is None:
            return None

        snapshots = [
            snapshot for snapshot in snapshots if snapshot.season_id == latest.season_id
        ][:11]
        snapshots.reverse()

        increases = 0
        comparisons = 0
        for previous, current in pairwise(snapshots):
            if previous.fp_per_game is None or current.fp_per_game is None:
                continue

            comparisons += 1
            if current.fp_per_game > previous.fp_per_game:
                increases += 1

        if comparisons == 0:
            return None
        return f"{increases}/{comparisons}"

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
