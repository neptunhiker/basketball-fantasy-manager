from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.forms import StyledFormMixin

from .models import PlayerNote, TeamNote


class PlayerNoteForm(StyledFormMixin, forms.ModelForm):
    """Private note a user keeps on a given player."""

    class Meta:
        model = PlayerNote
        fields = ["content"]
        widgets = {
            "content": forms.Textarea(
                attrs={
                    "rows": 4,
                    "placeholder": _("Keep a private note about this player's role, minutes, matchup, or trend."),
                }
            )
        }
        labels = {"content": _("Private note")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["content"].help_text = _("Only you can see this note.")

class TeamNoteForm(StyledFormMixin, forms.ModelForm):
    """Private note a user keeps on a given team."""

    class Meta:
        model = TeamNote
        fields = ["content"]
        widgets = {
            "content": forms.Textarea(
                attrs={
                    "rows": 4,
                    "placeholder": _("Keep a private note about this team's rotation, matchups, or trends."),
                }
            )
        }
        labels = {"content": _("Private note")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["content"].help_text = _("Only you can see this note.")


class BbdeLoginForm(StyledFormMixin, forms.Form):
    """The basketball.de login for one import. Never saved anywhere."""

    username = forms.CharField(
        label=_("basketball.de username"),
        max_length=150,
        widget=forms.TextInput(attrs={"autocomplete": "username"}),
    )
    # render_value=False: a form re-shown after an error must not echo the
    # password back into the page.
    password = forms.CharField(
        label=_("basketball.de password"),
        strip=False,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "current-password"}),
    )
