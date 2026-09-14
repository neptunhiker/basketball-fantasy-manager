from django import forms

from apps.core.forms import StyledFormMixin

from .models import PlayerNote


class PlayerNoteForm(StyledFormMixin, forms.ModelForm):
    """Private note a user keeps on a given player."""

    class Meta:
        model = PlayerNote
        fields = ["content"]
        widgets = {
            "content": forms.Textarea(
                attrs={
                    "rows": 4,
                    "placeholder": "Keep a private note about this player's role, minutes, matchup, or trend.",
                }
            )
        }
        labels = {"content": "Private note"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["content"].help_text = "Only you can see this note."
