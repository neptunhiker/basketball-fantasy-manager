"""The superuser pages for uploading official season stats.

Upload once, read the preview, then import or discard it. The file itself is
never kept: its matched rows wait on the `SeasonStatsImport` between preview
and confirmation (see `season_stats`). Superusers only, on every view -- the
button on the players list decides whether to offer the door, not whether it
opens.
"""

import uuid
from decimal import Decimal

from django import forms
from django.contrib import messages
from django.db.models import Count, Max
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views import View
from django.views.generic import DetailView

from apps.core.forms import StyledFormMixin
from apps.core.views import SuperuserRequiredMixin

from .models import Player, PlayerSeasonStats, SeasonStatsImport
from .season_stats import (
    MAX_FILE_BYTES,
    PREVIEW_LIFETIME,
    PreviewClosed,
    SeasonStatsFileError,
    confirm_season_stats,
    discard_season_stats,
    expire_stale_previews,
    name_key,
    preview_season_stats,
    validate_season_label,
)

RECENT_UPLOADS = 10
SAMPLE_SIZE = 8


def last_finished_season(today=None):
    """The season a new file most likely covers: the one that ended last.

    NBA seasons run October to June, so from July on the season that started
    last autumn is complete.
    """
    today = today or timezone.localdate()
    start = today.year - 1 if today.month >= 7 else today.year - 2
    return f"{start}-{(start + 1) % 100:02d}"


class SeasonStatsUploadForm(StyledFormMixin, forms.Form):
    season = forms.CharField(
        label=gettext_lazy("Season"),
        max_length=7,
        help_text=gettext_lazy("The NBA season the file covers, for example 2025-26."),
    )
    file = forms.FileField(label=gettext_lazy("CSV file"))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # A season label is seven characters; a full-width box looks empty.
        season = self.fields["season"].widget
        season.attrs["class"] += " sm:max-w-[12rem]"
        season.attrs.update(autocomplete="off", spellcheck="false", placeholder="2025-26")

    def clean_season(self):
        season = self.cleaned_data["season"].strip()
        try:
            validate_season_label(season)
        except SeasonStatsFileError as exc:
            raise forms.ValidationError(str(exc)) from exc
        return season

    def clean_file(self):
        file = self.cleaned_data["file"]
        if file.size > MAX_FILE_BYTES:
            raise forms.ValidationError(_("The file is larger than 2 MB."))
        return file


class SeasonStatsUploadView(SuperuserRequiredMixin, View):
    template_name = "nba/season_stats_upload.html"

    def get(self, request):
        form = SeasonStatsUploadForm(initial={"season": last_finished_season()})
        return self._render(form)

    def post(self, request):
        form = SeasonStatsUploadForm(request.POST, request.FILES)
        if form.is_valid():
            upload = form.cleaned_data["file"]
            try:
                run = preview_season_stats(
                    upload.read(),
                    season=form.cleaned_data["season"],
                    file_name=upload.name,
                    triggered_by=request.user,
                )
            except SeasonStatsFileError as exc:
                form.add_error("file", str(exc))
            else:
                return redirect("nba:season-stats-import", pk=run.pk)
        return self._render(form, status=400)

    def _render(self, form, status=200):
        expire_stale_previews()
        stored = (
            PlayerSeasonStats.objects.values("season")
            .annotate(players=Count("id"), updated=Max("updated_at"))
            .order_by("-season")
        )
        uploads = SeasonStatsImport.objects.select_related("triggered_by")[:RECENT_UPLOADS]
        return render(
            self.request,
            self.template_name,
            {"form": form, "stored_seasons": stored, "uploads": uploads},
            status=status,
        )


class SeasonStatsImportView(SuperuserRequiredMixin, DetailView):
    """One upload: its preview while open, its result afterwards."""

    model = SeasonStatsImport
    template_name = "nba/season_stats_import.html"
    context_object_name = "run"

    def get_object(self, queryset=None):
        expire_stale_previews()
        return super().get_object(queryset)

    def get_queryset(self):
        return SeasonStatsImport.objects.select_related("triggered_by")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        run = self.object
        lines = _stat_lines(run)
        is_open = run.status == SeasonStatsImport.Status.PREVIEWED
        context.update(
            is_open=is_open,
            is_uploader=run.triggered_by_id == self.request.user.pk,
            expires_at=run.created_at + PREVIEW_LIFETIME,
            matched_percent=round(100 * run.matched / run.rows) if run.rows else 0,
            sample=sorted(lines, key=lambda line: line["fantasy_points"], reverse=True)[
                :SAMPLE_SIZE
            ],
            renamed=sorted(
                (line for line in lines if line["renamed"]), key=lambda line: line["file_name"]
            ),
        )
        return context


# What the sample table shows of each line.
SHOWN = ("games_played", "minutes", "points", "rebounds", "assists", "fantasy_points")


def _stat_lines(run):
    """The rows behind a run, alike for an open preview and a finished import.

    A preview still holds them in its payload; an import has written them, and
    what is left of it is whatever this run last wrote.
    """
    if run.status == SeasonStatsImport.Status.PREVIEWED:
        players = Player.objects.in_bulk([item["player"] for item in run.payload])
        rows = [
            (players.get(uuid.UUID(item["player"])), item["name"], item["team"], item["values"])
            for item in run.payload
        ]
    else:
        rows = [
            (
                stats.player,
                stats.source_name,
                stats.team_abbreviation,
                {name: getattr(stats, name) for name in SHOWN},
            )
            for stats in run.stats.select_related("player")
        ]
    return [
        {
            "player": player,
            "file_name": file_name,
            "team": team,
            "renamed": name_key(file_name) != name_key(player.full_name),
            # The payload keeps decimals as strings; Decimal takes either.
            **{name: Decimal(str(values[name])) for name in SHOWN},
        }
        for player, file_name, team, values in rows
        if player is not None
    ]


class SeasonStatsConfirmView(SuperuserRequiredMixin, View):
    def post(self, request, pk):
        run = get_object_or_404(SeasonStatsImport, pk=pk)
        try:
            run = confirm_season_stats(run, request.user)
        except PreviewClosed as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request,
                _("Imported %(count)d players for %(season)s.")
                % {"count": run.matched, "season": run.season},
            )
        return redirect("nba:season-stats-import", pk=pk)


class SeasonStatsDiscardView(SuperuserRequiredMixin, View):
    def post(self, request, pk):
        run = get_object_or_404(SeasonStatsImport, pk=pk)
        try:
            discard_season_stats(run, request.user)
        except PreviewClosed as exc:
            messages.error(request, str(exc))
            return redirect("nba:season-stats-import", pk=pk)
        messages.info(request, _("The preview was discarded. Nothing was saved."))
        return redirect("nba:season-stats-upload")
