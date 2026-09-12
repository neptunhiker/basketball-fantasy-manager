"""Forms for the game layer. One so far: the season editor.

Seasons are the only records here an administrator edits by hand. Everything
else is either played into existence -- rosters, transactions -- or imported.
"""

from django import forms
from django.db import transaction

from apps.core.forms import StyledFormMixin

from .models import Season


class SeasonForm(StyledFormMixin, forms.ModelForm):
    """Create or edit a season, including the moment signings close.

    The cutoff is on this form and nowhere else in the app, which is the point:
    closing signings is a decision about the game everyone is playing, not
    about anybody's roster. `Season.clean` still checks it falls inside the
    season -- a ModelForm runs the model's own validation, so that rule lives
    once.
    """

    # Declared rather than taken from the model, and that is load-bearing.
    # Django validates a model's constraints during form validation, so an
    # instance carrying `is_current=True` while another season still holds it
    # would be rejected by `only_one_current_season` before `save` ever got the
    # chance to hand the flag over. Kept off the instance until then, the
    # illegal intermediate state never exists to be validated.
    is_current = forms.BooleanField(
        required=False,
        label="The season the app works with",
        help_text=(
            "Only one season can hold this. Ticking it here takes it off "
            "whichever season has it now."
        ),
    )

    # Meta.fields first, then the declared ones, so this is what puts the
    # checkbox back between the dates and the cutoff.
    field_order = ["label", "starts_on", "ends_on", "is_current", "signings_close_at"]

    class Meta:
        model = Season
        fields = ["label", "starts_on", "ends_on", "signings_close_at"]
        widgets = {
            # Native pickers. Without an explicit format the browser gets a
            # plain text box and the value it posts back is anyone's guess.
            "starts_on": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "ends_on": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            # `datetime-local` has no timezone of its own, which is right here:
            # Django reads and writes it in the active timezone, so an admin
            # types the wall-clock time the deadline actually falls at.
            "signings_close_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
        }
        help_texts = {
            "label": "How the season is written in the official game, e.g. 2026-27.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The checkbox is not a model field here, so nothing fills it in.
        self.fields["is_current"].initial = self.instance.is_current

    def clean(self):
        """The one rule the database states but cannot explain.

        `season_ends_after_it_starts` is a check constraint, so a reversed pair
        would be refused either way -- as an IntegrityError, which is a 500 and
        tells the administrator nothing. This is the same rule said in a
        sentence, early enough to be shown next to the field.
        """
        cleaned = super().clean()
        starts_on, ends_on = cleaned.get("starts_on"), cleaned.get("ends_on")
        if starts_on and ends_on and ends_on <= starts_on:
            self.add_error("ends_on", "A season has to end after it starts.")
        return cleaned

    @transaction.atomic
    def save(self, commit=True):
        """Saves, and hands `is_current` over from whichever season had it.

        `only_one_current_season` is a partial unique constraint, so two
        current seasons is an IntegrityError rather than a question. Ticking
        the box therefore has to mean "move it here", which is what an
        administrator means by it.

        The handover happens *before* the save, and that order is the whole
        trick: saving first would be the second current season the constraint
        exists to refuse, and the IntegrityError would land before this code
        got the chance to tidy up. The transaction is what keeps the moment
        when no season is current from being observable.

        Excluding this season from that sweep is tidiness rather than
        correctness: the save below re-asserts the flag either way, so the
        only difference is one pointless write to the row about to be written.
        It reads as what is meant -- take it off the *others* -- which is worth
        the line.
        """
        make_current = self.cleaned_data.get("is_current", False)
        if commit and make_current:
            Season.objects.filter(is_current=True).exclude(pk=self.instance.pk).update(
                is_current=False
            )
        # Set here rather than by `construct_instance`, for the reason given on
        # the field: until this line the instance still carries whatever the
        # database says, which is what keeps validation legal.
        self.instance.is_current = make_current
        return super().save(commit=commit)
