"""The staff forms on the Reports page: start a run, rerun one, settings.

Only validation lives here; the page draws the choices itself (model cards,
effort pills), which is why the run forms do not use `StyledFormMixin`.
"""

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.forms import INPUT_CLASSES, StyledFormMixin
from apps.fantasy.models import Roster

from . import catalog
from .models import Effort, ReportSettings

ACTIVE, CHOSEN, BRIEFING = "active", "chosen", "briefing"


class RunForm(forms.Form):
    model = forms.ChoiceField(choices=catalog.choices)
    effort = forms.ChoiceField(choices=Effort.choices)
    label = forms.CharField(
        max_length=80,
        required=False,
        widget=forms.TextInput(
            attrs={"class": INPUT_CLASSES, "placeholder": _("e.g. Sol at high effort")}
        ),
    )
    scope = forms.ChoiceField(
        choices=[
            (ACTIVE, _("All active rosters")),
            (CHOSEN, _("Chosen rosters")),
            (BRIEFING, _("Briefing only")),
        ],
        initial=ACTIVE,
    )
    rosters = forms.ModelMultipleChoiceField(queryset=Roster.objects.none(), required=False)

    def __init__(self, *args, season, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["rosters"].queryset = Roster.objects.filter(season=season)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("scope") == CHOSEN and not cleaned.get("rosters"):
            self.add_error("rosters", _("Choose at least one roster."))
        return cleaned

    def run_options(self):
        """What `services.start_run` takes, from the cleaned form."""
        scope = self.cleaned_data["scope"]
        return {
            "model": self.cleaned_data["model"],
            "effort": self.cleaned_data["effort"],
            "label": self.cleaned_data["label"],
            "briefing_only": scope == BRIEFING,
            "rosters": list(self.cleaned_data["rosters"]) if scope == CHOSEN else None,
        }


class RerunForm(forms.Form):
    model = forms.ChoiceField(choices=catalog.choices)
    effort = forms.ChoiceField(choices=Effort.choices)
    label = forms.CharField(max_length=80, required=False)


class SettingsForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = ReportSettings
        fields = ["default_model", "default_effort", "sunday_run_enabled"]
        labels = {
            "default_model": _("Default model"),
            "default_effort": _("Default reasoning effort"),
            "sunday_run_enabled": _("Run and publish a report every Sunday at 12:30"),
        }
