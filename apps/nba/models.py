"""Real-world basketball facts: who exists and which team they play for.

Nothing in here knows about salaries, caps or rosters. These rows describe the
NBA and would still be true if this app did not exist, which is what keeps them
separate from the fantasy game layer.
"""

import uuid

from django.db import models
from django.utils.text import slugify

from apps.core.models import TimeStampedModel


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
    abbreviation = models.CharField("Kürzel", max_length=4, unique=True)
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
    first_name = models.CharField("Vorname", max_length=64, blank=True)
    last_name = models.CharField("Nachname", max_length=64)
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
        "Aktiv",
        default=True,
        help_text="Cleared by the import when a player stops appearing in the source.",
    )

    class Meta:
        ordering = ["last_name", "first_name"]
        verbose_name = "Spieler"
        verbose_name_plural = "Spieler"
        indexes = [
            models.Index(fields=["last_name", "first_name"]),
            models.Index(fields=["position"]),
        ]

    def __str__(self):
        return self.full_name

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

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
        base = slugify(self.full_name) or "spieler"
        slug = base
        suffix = 2
        while Player.objects.filter(slug=slug).exclude(pk=self.pk).exists():
            slug = f"{base}-{suffix}"
            suffix += 1
        return slug
