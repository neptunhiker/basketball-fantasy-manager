import datetime as dt

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import ValidationError
from django.db import connection
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from apps.fantasy.models import ROSTER_SIZE, Roster, Season


def _countdown_for_season(season):
    """Return the next meaningful deadline in the season lifecycle."""
    if season is None:
        return None, ""

    now = timezone.now()
    current_zone = timezone.get_current_timezone()
    season_start = timezone.make_aware(dt.datetime.combine(season.starts_on, dt.time.min), current_zone)
    season_end = timezone.make_aware(dt.datetime.combine(season.ends_on, dt.time.max), current_zone)
    signings_open = season.signings_open_at
    signings_close = season.signings_close_at

    if signings_open and now < signings_open:
        return signings_open, "Signings open in"
    if signings_close and now < signings_close:
        return signings_close, "Signings close in"
    if now < season_start:
        return season_start, "Season starts in"
    if now <= season_end:
        return season_end, "Season ends in"
    return None, ""


COUNTDOWN_COPY = {
    "Signings open in": (
        "The draft room opens soon.",
        "Get your shortlist ready, then start building when signings open.",
    ),
    "Signings close in": (
        "Complete your roster before signings close.",
        "Make your final additions before the signing window shuts.",
    ),
    "Season starts in": (
        "Set your lineup before tip-off.",
        "Signings are closed. Make sure your roster is ready for the season.",
    ),
    "Season ends in": (
        "Make every move count.",
        "The season is live. Keep an eye on your roster until the final day.",
    ),
}


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "core/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        season = Season.objects.filter(is_current=True).first()
        context["current_season"] = season
        context["days_to_season_start"] = (
            (season.starts_on - timezone.localdate()).days
            if season and season.timing == Season.Timing.UPCOMING
            else None
        )
        context["transactions_allowed"] = season.transactions_allowed if season else False
        context["trading_allowed"] = season.trading_allowed if season else False
        context["trading_window_open"] = context["trading_allowed"]
        countdown_target, countdown_label = _countdown_for_season(season)
        context["countdown_target_at"] = (
            timezone.localtime(countdown_target) if countdown_target else None
        )
        context["countdown_label"] = countdown_label
        context["countdown_heading"], context["countdown_description"] = COUNTDOWN_COPY.get(
            countdown_label, ("The season is underway.", "Keep your roster moving.")
        )
        dashboard_rosters = (
            list(
                Roster.objects.filter(manager__user=self.request.user, season=season)
                .with_squad()
                .order_by("name")
            )
            if season
            else []
        )
        context["dashboard_roster"] = dashboard_rosters[0] if dashboard_rosters else None
        context["dashboard_roster_count"] = len(dashboard_rosters)
        context["roster_size"] = ROSTER_SIZE
        return context


def healthz(request):
    """Liveness/readiness probe for Fly. Checks the database is reachable."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        return JsonResponse({"status": "error", "database": "unreachable"}, status=503)
    return JsonResponse({"status": "ok"})


class StaffRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Only team members administer things that belong to everybody.

    Here rather than in `accounts` because it is not about accounts: `accounts`
    happened to be the first app that needed it, and `fantasy` needs the same
    answer for the season editor. What counts as staff is one decision and
    should have one home.
    """

    def test_func(self):
        return self.request.user.is_staff


class AdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Only staff superusers may administer global configuration."""

    def test_func(self):
        return self.request.user.is_staff and self.request.user.is_superuser


class TypedConfirmView(LoginRequiredMixin, View):
    """A modal that only proceeds once the user has typed a word out.

    Subclass it, set the strings, implement three methods. No template needed --
    `partials/modal_confirm.html` renders entirely from context:

        class RosterDeleteView(OwnRosterMixin, TypedConfirmView):
            title = "Delete this roster?"
            confirm_label = "Delete permanently"

            def get_object(self):
                return self.get_roster()

            def get_body(self, obj):
                return f'"{obj.name}" will be deleted.'

            def perform(self, obj):
                obj.delete()

            def get_success_url(self, obj):
                return reverse("fantasy:roster-list")

    GET renders the modal, POST checks the typed word and acts. The check is
    repeated here rather than trusted from the disabled button, because a
    disabled button stops nobody holding a terminal.
    """

    template_name = "partials/modal_confirm.html"
    confirm_word = "DELETE"
    title = ""
    confirm_label = "Confirm"
    cancel_label = "Cancel"
    tone = "danger"

    def get_object(self):
        raise NotImplementedError

    def get_body(self, obj):
        return ""

    def perform(self, obj):
        raise NotImplementedError

    def get_success_url(self, obj):
        raise NotImplementedError

    def get_context(self, obj, error=None):
        return {
            "title": self.title,
            "body": self.get_body(obj),
            "action_url": self.request.path,
            "confirm_word": self.confirm_word,
            "confirm_label": self.confirm_label,
            "cancel_label": self.cancel_label,
            "tone": self.tone,
            "error": error,
        }

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name, self.get_context(self.get_object()))

    def post(self, request, *args, **kwargs):
        obj = self.get_object()

        if request.POST.get("confirm", "").strip() != self.confirm_word:
            # Deliberately a 200: HTMX only swaps successful responses by
            # default, and this response *is* the corrected form.
            return render(
                request,
                self.template_name,
                self.get_context(obj, error=f'Type "{self.confirm_word}" exactly to confirm.'),
            )

        # Read the URL before acting -- afterwards the object may be gone.
        success_url = self.get_success_url(obj)
        try:
            self.perform(obj)
        except ValidationError as exc:
            # A refusal the confirm step could not have known about: state that
            # changed between the modal opening and this POST, or a database
            # constraint that is the real authority. Re-rendered with the
            # message, as PromptView and ModalFormView both do -- a 500 would
            # be the only honest alternative, and it explains nothing.
            return render(request, self.template_name, self.get_context(obj, error=exc.messages[0]))

        response = HttpResponse(status=204)
        response["HX-Redirect"] = success_url
        return response


class PromptView(LoginRequiredMixin, View):
    """A modal that asks for one short piece of text, and optionally one choice.

    The counterpart to TypedConfirmView: subclass it, set the strings, and
    `partials/modal_prompt.html` renders entirely from context, so a new prompt
    needs a view and no template:

        class RosterCreateView(PromptView):
            title = "New roster"
            field_label = "What should the roster be called?"
            field_name = "name"
            submit_label = "Create roster"

            def initial_value(self, obj):
                return services.default_roster_name(...)

            def perform(self, obj, value, choice=None):
                return Roster.objects.create(name=value, ...)

            def get_success_url(self, result):
                return reverse("fantasy:roster-rules", args=[result.pk])

    GET renders the modal with `initial_value` prefilled, POST cleans the value
    and acts. Validation lives in `clean`, which may raise ValidationError to
    send the user back to a filled-in form carrying the message.

    The choice is the "and where does it go" half -- a roster under a manager,
    say. Set `choice_name` and return options from `get_choices`; return an
    empty list and the select is not rendered at all, which is how a prompt with
    nothing to decide stays a single field. `clean_choice` turns the submitted
    value into whatever `perform` wants, and is the only place that decides
    whether the option was one the user was actually offered -- a select is a
    suggestion to the browser, not a constraint on what a POST may carry.
    """

    template_name = "partials/modal_prompt.html"
    title = ""
    body = ""
    field_label = "Name"
    field_name = "value"
    placeholder = ""
    max_length = 80
    help_text = ""
    submit_label = "Save"
    cancel_label = "Cancel"

    # Optional second field. Falsy `choice_name` means there is no select.
    choice_name = ""
    choice_label = ""
    choice_help = ""

    def get_object(self):
        """The thing being edited, or None when the prompt creates something."""
        return None

    def initial_value(self, obj):
        return ""

    def get_choices(self):
        """`[(value, label)]` for the select, or `[]` to leave it out."""
        return []

    def default_choice(self):
        """Which option starts selected. `""` selects the first one."""
        return ""

    def clean_choice(self, raw):
        """Turn the submitted option into what `perform` needs.

        Raise ValidationError if it is not one this user may pick. Nothing else
        checks: the rendered options are a courtesy to the browser and say
        nothing about what a hand-written POST can contain.
        """
        return raw

    def clean(self, value, obj, choice=None):
        """Return the value to use, or raise ValidationError with a message."""
        value = value.strip()
        if not value:
            raise ValidationError("Please enter a name.")
        return value[: self.max_length]

    def perform(self, obj, value, choice=None):
        """Do the work. Whatever this returns is handed to get_success_url."""
        raise NotImplementedError

    def get_success_url(self, result):
        raise NotImplementedError

    def get_context(self, value, error=None, choice_value=None):
        choices = self.get_choices() if self.choice_name else []
        return {
            "title": self.title,
            "body": self.body,
            "action_url": self.request.path,
            "field_label": self.field_label,
            "field_name": self.field_name,
            "value": value,
            "placeholder": self.placeholder,
            "max_length": self.max_length,
            "help_text": self.help_text,
            "submit_label": self.submit_label,
            "cancel_label": self.cancel_label,
            "error": error,
            "choice_name": self.choice_name,
            "choice_label": self.choice_label,
            "choice_help": self.choice_help,
            "choices": choices,
            # On a re-render after an error this is what the user had picked,
            # so a rejected name does not also lose their choice.
            "choice_value": self.default_choice() if choice_value is None else choice_value,
        }

    def get(self, request, *args, **kwargs):
        obj = self.get_object()
        return render(request, self.template_name, self.get_context(self.initial_value(obj)))

    def post(self, request, *args, **kwargs):
        obj = self.get_object()
        raw = request.POST.get(self.field_name, "")
        raw_choice = request.POST.get(self.choice_name, "") if self.choice_name else ""

        try:
            # The choice first: the name may only be valid or invalid relative
            # to it, as a roster name is, being unique per manager.
            choice = self.clean_choice(raw_choice)
            value = self.clean(raw, obj, choice)
            result = self.perform(obj, value, choice)
        except ValidationError as exc:
            # Deliberately a 200: HTMX only swaps successful responses by
            # default, and this response *is* the corrected form. The value is
            # echoed back so the user does not retype what they just wrote.
            return render(
                request,
                self.template_name,
                self.get_context(raw, error=exc.messages[0], choice_value=raw_choice),
            )

        success_url = self.get_success_url(result)
        if not request.htmx:
            # The prompt is progressive enhancement: a plain form post (or a
            # script) still works, and a plain redirect is what it expects.
            return redirect(success_url)

        response = HttpResponse(status=204)
        response["HX-Redirect"] = success_url
        return response


class ModalFormView(LoginRequiredMixin, View):
    """A modal that holds a whole form, for a record with more than one field.

    The third of the modal views, and the one for when `PromptView` runs out:
    a prompt asks for one string and optionally one choice, which covers a name
    or a rename but not a record with dates and a checkbox on it. Subclass it,
    point `form_class` at a ModelForm, and `partials/modal_form.html` renders
    every field through the same `partials/form_field.html` the full-page forms
    use -- so a field's label, help text and errors look the same wherever it
    is shown.

        class SeasonCreateView(StaffRequiredMixin, ModalFormView):
            form_class = SeasonForm
            title = "New season"

            def get_success_url(self, obj):
                return reverse("fantasy:season-list")

    `get_object` returning None makes it a create form; returning an instance
    makes it an edit. Success is a 204 with `HX-Redirect`, and a form with
    errors comes back as a 200 -- for the same reason `PromptView` does it:
    HTMX only swaps successful responses, and the response *is* the corrected
    form.
    """

    template_name = "partials/modal_form.html"
    form_class = None
    title = ""
    body = ""
    submit_label = "Save"
    cancel_label = "Cancel"

    def get_object(self):
        """The record being edited, or None when the form creates one."""
        return None

    def get_form(self, data=None):
        form = self.form_class(data=data, instance=self.get_object())
        # The modal shell focuses `[data-autofocus]` when it opens. Marked here
        # rather than on the form, because wanting focus is a property of being
        # in a dialog -- the same form on a full page should not steal it.
        for field in form.visible_fields():
            field.field.widget.attrs["data-autofocus"] = ""
            break
        return form

    def perform(self, form):
        """Commit the form. Whatever this returns is handed to get_success_url."""
        return form.save()

    def get_success_url(self, obj):
        raise NotImplementedError

    def get_context(self, form):
        return {
            "title": self.title,
            "body": self.body,
            "action_url": self.request.path,
            "form": form,
            "submit_label": self.submit_label,
            "cancel_label": self.cancel_label,
            "panel_class": "modal-panel-wide",
        }

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name, self.get_context(self.get_form()))

    def post(self, request, *args, **kwargs):
        form = self.get_form(data=request.POST)
        if form.is_valid():
            try:
                result = self.perform(form)
            except ValidationError as exc:
                # A rule the form could not check on its own -- one that needs
                # the write to be attempted. Attached to the form so it renders
                # where every other error on this modal renders.
                form.add_error(None, exc)
            else:
                success_url = self.get_success_url(result)
                if not request.htmx:
                    return redirect(success_url)
                response = HttpResponse(status=204)
                response["HX-Redirect"] = success_url
                return response

        return render(request, self.template_name, self.get_context(form))
