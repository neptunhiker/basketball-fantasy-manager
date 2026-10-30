import uuid

from django.db import models

from apps.core.models import TimeStampedModel
from apps.fantasy.models import Season
from apps.nba.models import Player


class DreamTeam(TimeStampedModel):
    """The Dream Team as one import left it. See `apps.analytics.dream_team`.

    One per season and import moment. A recalculation for the same moment
    overwrites it, so `updated_at` is when it was last worked out. Keeping one
    per import is what lets the page say who came in and who went out.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    season = models.ForeignKey(
        Season, verbose_name="Season", on_delete=models.CASCADE, related_name="dream_teams"
    )
    # The import whose points were used. Prices always come from draft day.
    as_of = models.DateTimeField("Points as of")
    draft_as_of = models.DateTimeField("Prices as of")
    # Copied rather than read from the season, so an edited budget does not
    # quietly change what an old result claims it was chosen under.
    budget = models.DecimalField("Budget", max_digits=12, decimal_places=2)
    total_salary = models.DecimalField("Total salary", max_digits=12, decimal_places=2)
    total_fp = models.DecimalField("Fantasy points", max_digits=9, decimal_places=2)
    candidates = models.PositiveIntegerField(
        "Candidates", help_text="Players with a draft-day price, i.e. who could have been picked."
    )

    class Meta:
        ordering = ["-as_of"]
        verbose_name = "Dream Team"
        verbose_name_plural = "Dream Teams"
        constraints = [
            models.UniqueConstraint(
                fields=["season", "as_of"], name="one_dream_team_per_season_and_moment"
            ),
        ]

    def __str__(self):
        return f"Dream Team {self.season} @ {self.as_of:%Y-%m-%d %H:%M}"

    @property
    def left_over(self):
        return self.budget - self.total_salary


class DreamTeamPick(TimeStampedModel):
    """One of a Dream Team's fifteen, with the numbers he was picked on.

    Price and position are draft day's, points are those at the team's
    `as_of`. Stored rather than looked up again, so an old result still reads
    the way it was decided even if snapshots are later re-imported.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dream_team = models.ForeignKey(
        DreamTeam, verbose_name="Dream Team", on_delete=models.CASCADE, related_name="picks"
    )
    player = models.ForeignKey(
        Player, verbose_name="Player", on_delete=models.PROTECT, related_name="dream_team_picks"
    )
    position = models.CharField("Position", max_length=1, choices=Player.Position)
    salary = models.DecimalField("Draft-day salary", max_digits=12, decimal_places=2)
    total_fp = models.DecimalField("Fantasy points", max_digits=8, decimal_places=2)
    games_played = models.PositiveSmallIntegerField("Games played")

    class Meta:
        ordering = ["-total_fp"]
        verbose_name = "Dream Team pick"
        verbose_name_plural = "Dream Team picks"
        constraints = [
            models.UniqueConstraint(
                fields=["dream_team", "player"], name="a_player_is_picked_once_per_dream_team"
            ),
        ]

    def __str__(self):
        return f"{self.player} ({self.dream_team})"

    @property
    def fp_per_million(self):
        """Points per $1M of draft-day salary. None for a free player."""
        if not self.salary:
            return None
        return self.total_fp / (self.salary / 1_000_000)
