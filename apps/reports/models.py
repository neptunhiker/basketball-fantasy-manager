"""AI trade reports: runs, the reports in them, and the trades they recommend.

A *run* is one model asked about one moment's data: a shared market briefing
plus one report per roster. Runs are kept, all of them, so staff can compare
models on the same week and decide which runs players get to see; publishing
is per run. See my_docs/AI_TRADE_REPORTS.md.
"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.core.models import TimeStampedModel
from apps.fantasy.models import Roster, Season
from apps.nba.models import Player

from . import catalog


class Effort(models.TextChoices):
    LOW = catalog.LOW, "Low"
    MEDIUM = catalog.MEDIUM, "Medium"
    HIGH = catalog.HIGH, "High"


class ReportSettings(TimeStampedModel):
    """The one row of report settings staff change in the app.

    What the automatic Sunday run uses. Created on first read with the
    catalog's defaults, so a fresh database needs no setup.
    """

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    default_model = models.CharField(
        "Default model", max_length=64, choices=catalog.choices, default=catalog.DEFAULT_MODEL
    )
    default_effort = models.CharField(
        "Default reasoning effort",
        max_length=8,
        choices=Effort.choices,
        default=catalog.DEFAULT_EFFORT,
    )
    sunday_run_enabled = models.BooleanField(
        "Sunday run",
        default=True,
        help_text="Generate and publish a report every Sunday at 12:30.",
    )

    class Meta:
        verbose_name = "Report settings"
        verbose_name_plural = "Report settings"
        constraints = [models.CheckConstraint(condition=Q(id=1), name="one_report_settings_row")]

    def __str__(self):
        return "Report settings"

    @classmethod
    def load(cls):
        return cls.objects.get_or_create(pk=1)[0]


class ReportRun(TimeStampedModel):
    """One model asked about one moment's data: a briefing plus roster reports.

    Keeps the exact shared input it sent, so the same data can be put to
    another model later (`trigger=rerun`) and every run can be explained.
    """

    class Trigger(models.TextChoices):
        SCHEDULED = "scheduled", "Automatic"
        MANUAL = "manual", "Manual"
        RERUN = "rerun", "Rerun"

    class Scope(models.TextChoices):
        FULL = "full", "Briefing and rosters"
        BRIEFING_ONLY = "briefing", "Briefing only"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        PARTIAL = "partial", "Partly failed"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    season = models.ForeignKey(
        Season, verbose_name="Season", on_delete=models.CASCADE, related_name="report_runs"
    )
    trigger = models.CharField("Started by", max_length=16, choices=Trigger.choices)
    started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Started by user",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    # The run this one put to another model, for `trigger=rerun`.
    source_run = models.ForeignKey(
        "self",
        verbose_name="Rerun of",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reruns",
    )
    model = models.CharField("Model", max_length=64)
    effort = models.CharField("Reasoning effort", max_length=8, choices=Effort.choices)
    label = models.CharField("Label", max_length=80, blank=True)
    prompt_version = models.CharField("Prompt version", max_length=16)
    scope = models.CharField("Scope", max_length=16, choices=Scope.choices, default=Scope.FULL)
    data_as_of = models.DateTimeField("Data as of")
    shared_input = models.TextField("Shared input")
    # Short ref ("P017") -> player id, for every player the input names.
    player_refs = models.JSONField("Player refs", default=dict)
    status = models.CharField(
        "Status", max_length=16, choices=Status.choices, default=Status.QUEUED
    )
    finished_at = models.DateTimeField("Finished at", null=True, blank=True)
    is_published = models.BooleanField("Published", default=False)
    published_at = models.DateTimeField("Published at", null=True, blank=True)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Published by",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Report run"
        verbose_name_plural = "Report runs"
        indexes = [models.Index(fields=["season", "is_published", "-created_at"])]

    def __str__(self):
        return f"{catalog.display_name(self.model)} @ {self.created_at:%Y-%m-%d %H:%M}"

    @property
    def model_name(self):
        return catalog.display_name(self.model)

    @property
    def is_active(self):
        return self.status in (self.Status.QUEUED, self.Status.RUNNING)

    @property
    def is_briefing_only(self):
        return self.scope == self.Scope.BRIEFING_ONLY


class Report(TimeStampedModel):
    """One answer in a run: the market briefing, or one roster's report."""

    class Kind(models.TextChoices):
        BRIEFING = "briefing", "Market briefing"
        ROSTER = "roster", "Roster report"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(
        ReportRun, verbose_name="Run", on_delete=models.CASCADE, related_name="reports"
    )
    kind = models.CharField("Kind", max_length=16, choices=Kind.choices)
    # Kept when the roster goes: the report still belongs to the run's history.
    roster = models.ForeignKey(
        Roster,
        verbose_name="Roster",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reports",
    )
    # The roster's own part of the input, exactly as sent (empty for the briefing).
    own_input = models.TextField("Roster input", blank=True)
    # The legal trades offered, as data: what the page draws trade cards from
    # and what an answer's trade ids are checked against.
    shortlist = models.JSONField("Shortlist", default=list, blank=True)
    status = models.CharField(
        "Status", max_length=16, choices=Status.choices, default=Status.PENDING
    )
    attempts = models.PositiveSmallIntegerField("Attempts", default=0)
    output = models.JSONField("Output", null=True, blank=True)
    error = models.TextField("Error", blank=True)
    input_tokens = models.PositiveIntegerField("Input tokens", default=0)
    cached_tokens = models.PositiveIntegerField("Cached input tokens", default=0)
    cache_write_tokens = models.PositiveIntegerField("Cache write tokens", default=0)
    output_tokens = models.PositiveIntegerField("Output tokens", default=0)
    reasoning_tokens = models.PositiveIntegerField("Reasoning tokens", default=0)
    cost_usd = models.DecimalField("Cost (USD)", max_digits=10, decimal_places=6, default=0)
    duration_ms = models.PositiveIntegerField("Duration (ms)", null=True, blank=True)
    response_id = models.CharField("Response ID", max_length=128, blank=True)
    started_at = models.DateTimeField("Started at", null=True, blank=True)
    finished_at = models.DateTimeField("Finished at", null=True, blank=True)

    class Meta:
        ordering = ["kind", "created_at"]
        verbose_name = "Report"
        verbose_name_plural = "Reports"
        constraints = [
            models.UniqueConstraint(
                fields=["run"],
                condition=Q(kind="briefing"),
                name="one_briefing_per_run",
            ),
            models.UniqueConstraint(
                fields=["run", "roster"],
                condition=Q(kind="roster"),
                name="one_report_per_roster_per_run",
            ),
        ]
        indexes = [models.Index(fields=["finished_at"])]

    def __str__(self):
        return f"{self.get_kind_display()} · {self.run}"

    @property
    def is_briefing(self):
        return self.kind == self.Kind.BRIEFING

    @property
    def is_done(self):
        return self.status in (self.Status.SUCCEEDED, self.Status.FAILED)


class RecommendedTrade(TimeStampedModel):
    """A trade a roster report recommends, as rows the track record can read.

    The salaries are the ones the advice was given at (`run.data_as_of`).
    """

    class Confidence(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    report = models.ForeignKey(
        Report, verbose_name="Report", on_delete=models.CASCADE, related_name="trades"
    )
    rank = models.PositiveSmallIntegerField("Priority")
    trade_ref = models.CharField("Shortlist ref", max_length=8)
    player_out = models.ForeignKey(
        Player, verbose_name="Player out", on_delete=models.PROTECT, related_name="+"
    )
    player_in = models.ForeignKey(
        Player, verbose_name="Player in", on_delete=models.PROTECT, related_name="+"
    )
    salary_out = models.DecimalField("Salary out", max_digits=12, decimal_places=2)
    salary_in = models.DecimalField("Salary in", max_digits=12, decimal_places=2)
    confidence = models.CharField("Confidence", max_length=8, choices=Confidence.choices)
    reasoning = models.TextField("Reasoning", blank=True)

    class Meta:
        ordering = ["rank"]
        verbose_name = "Recommended trade"
        verbose_name_plural = "Recommended trades"
        constraints = [
            models.UniqueConstraint(fields=["report", "rank"], name="one_trade_per_rank"),
        ]

    def __str__(self):
        return f"{self.player_out} → {self.player_in}"
