import datetime as dt
from collections import Counter
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db.models import (
    BooleanField,
    Count,
    ExpressionWrapper,
    IntegerField,
    OuterRef,
    Prefetch,
    Q,
    Subquery,
)
from django.db.models.deletion import ProtectedError
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import number_format
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.utils.translation import gettext_lazy as _lazy
from django.views import View
from django.views.generic import DetailView, ListView, TemplateView

from apps.core.dates import local_date
from apps.core.htmx import add_toast
from apps.core.templatetags.money import millions
from apps.core.views import (
    AdminRequiredMixin,
    ConfirmView,
    ModalFormView,
    PromptView,
    StaffRequiredMixin,
    TypedConfirmView,
)
from apps.nba.models import Player, Team
from apps.nba.stats import DESCENDING_FIRST

from . import services
from .forms import SeasonForm, TradeGrantForm
from .models import (
    MINIMUM_BY_POSITION,
    ROSTER_SIZE,
    ROSTER_ICON_CHOICES,
    TRADE_BUY_PRICE,
    TRADE_SELL_PRICE,
    Manager,
    PlayerSnapshot,
    Roster,
    RosterPlayer,
    Season,
    TradeGrant,
)


def _count_per_season(model):
    """A correlated count, rather than a second Count() on the same queryset.

    Two Count() annotations join both tables at once and multiply the rows, so
    they need distinct=True to be correct -- and with tens of thousands of
    snapshots per season, that cross join is slow enough to be felt on a page
    that only ever shows a handful of rows.
    """
    return Coalesce(
        Subquery(
            model.objects.filter(season=OuterRef("pk"))
            .order_by()
            .values("season")
            .annotate(n=Count("pk"))
            .values("n"),
            output_field=IntegerField(),
        ),
        0,
    )


class SeasonListView(AdminRequiredMixin, ListView):
    """The season administration page is limited to staff superusers.

    The counts are the point of the table rather than decoration: they are what
    makes a season deletable or not, so a row shows why before anyone tries.
    """

    model = Season
    template_name = "fantasy/season_list.html"
    context_object_name = "seasons"

    def get_queryset(self):
        return Season.objects.annotate(
            roster_count=_count_per_season(Roster),
            snapshot_count=_count_per_season(PlayerSnapshot),
        )

    def get_context_data(self, **kwargs):
        # Whether to render the edit controls at all. Not the check that
        # matters -- each of those views has its own -- just whether to offer
        # a door that would be shut.
        return {**super().get_context_data(**kwargs), "can_manage": True}


class SeasonFormView(AdminRequiredMixin, ModalFormView):
    """The half the create and edit dialogs share: the form, and where to go.

    Staff superusers only, and that is the whole authorisation story for seasons: a season
    is not owned by anybody, so there is no per-object rule to write. Unlike
    the roster and manager views, which scope to the account behind them.
    """

    form_class = SeasonForm

    def get_success_url(self, obj):
        return reverse("fantasy:season-list")


class SeasonCreateView(SeasonFormView):
    title = _lazy("New season")
    body = (
        "A season is the frame everything else sits in: rosters belong to one, "
        "and so does every imported price."
    )
    submit_label = _lazy("Create season")


class SeasonUpdateView(SeasonFormView):
    title = _lazy("Edit season")
    submit_label = _lazy("Save changes")

    def get_object(self):
        if not hasattr(self, "_season"):
            self._season = get_object_or_404(Season, pk=self.kwargs["pk"])
        return self._season


class SeasonTradeGrantsView(AdminRequiredMixin, View):
    """The one-off free trades of a season, listed, added and removed in a modal.

    The weekly trades are not here: they are two fields on the season itself.
    A grant that has already been paid out stays in every roster's history
    when it is deleted -- deleting it only stops rosters that have not yet
    received it from getting it.
    """

    template_name = "fantasy/partials/season_trade_grants.html"

    def get_season(self):
        return get_object_or_404(Season, pk=self.kwargs["pk"])

    def render(self, season, form=None):
        return render(
            self.request,
            self.template_name,
            {
                "season": season,
                "grants": season.trade_grants.all(),
                "form": form or TradeGrantForm(),
                "weekly_count": len(season.weekly_trade_moments()),
            },
        )

    def get(self, request, *args, **kwargs):
        return self.render(self.get_season())

    def post(self, request, *args, **kwargs):
        season = self.get_season()
        if request.POST.get("action") == "delete":
            TradeGrant.objects.filter(season=season, pk=request.POST.get("grant")).delete()
            return self.render(season)
        form = TradeGrantForm(request.POST)
        if form.is_valid():
            form.instance.season = season
            form.save()
            return self.render(season)
        return self.render(season, form)


class SeasonDeleteView(AdminRequiredMixin, TypedConfirmView):
    """Deletes a season -- with three different answers, set by the model.

    `Roster.season` is PROTECT and `PlayerSnapshot.season` is CASCADE, and that
    difference is the whole design here rather than a policy this view invents:

      * rosters exist -> refused. The database would refuse anyway; this says
        so in a sentence instead of raising ProtectedError at a user.
      * snapshots but no rosters -> allowed, with DELETE typed out. The whole
        price history of the season goes, which is the largest single
        destruction the app can do.
      * neither -> a plain confirm. There is nothing to lose but the row.
    """

    title = _lazy("Delete this season?")
    confirm_label = _lazy("Delete season")

    def get_object(self):
        if not hasattr(self, "_season"):
            self._season = get_object_or_404(Season, pk=self.kwargs["pk"])
        return self._season

    def counts(self, obj):
        if not hasattr(self, "_counts"):
            self._counts = (obj.rosters.count(), obj.snapshots.count())
        return self._counts

    @property
    def confirm_word(self):
        """Typed out only when there is history to lose.

        An empty season costs a row, and friction that guards nothing teaches
        people to type DELETE without reading -- which is exactly the habit
        that would hurt on a season holding a year of prices.
        """
        _, snapshots = self.counts(self.get_object())
        return "DELETE" if snapshots else ""

    def get_context(self, obj, error=None):
        context = super().get_context(obj, error=error)
        rosters, _snapshots = self.counts(obj)
        if rosters:
            # No confirm button at all rather than one that fails: the modal is
            # explaining why this cannot be done.
            context["confirm_label"] = ""
            context["cancel_label"] = _("Close")
        return context

    def get_body(self, obj):
        rosters, snapshots = self.counts(obj)
        if rosters:
            return ngettext(
                "%(season)s still holds %(count)d roster. Delete it first -- a "
                "roster's whole transaction history hangs off its season, and the "
                "database will not let the season go while it exists.",
                "%(season)s still holds %(count)d rosters. Delete those first -- a "
                "roster's whole transaction history hangs off its season, and the "
                "database will not let the season go while they exist.",
                rosters,
            ) % {"season": obj.label, "count": rosters}
        if snapshots:
            return ngettext(
                "%(season)s holds %(count)s imported price and no rosters. Deleting "
                "the season deletes it -- the salary history for that year, which "
                "nothing can rebuild.",
                "%(season)s holds %(count)s imported prices and no rosters. Deleting "
                "the season deletes every one of them -- the entire salary history "
                "for that year, which nothing can rebuild.",
                snapshots,
            ) % {"season": obj.label, "count": number_format(snapshots, force_grouping=True)}
        return _(
            "%(season)s holds no rosters and no prices, so nothing goes with it. "
            "The season itself is gone for good, though."
        ) % {"season": obj.label}

    def post(self, request, *args, **kwargs):
        obj = self.get_object()
        rosters, _snapshots = self.counts(obj)
        if rosters:
            # Reachable without the button -- a hand-written POST, or a dialog
            # left open while a roster was created. Re-rendered rather than a
            # 403, because the modal already says why.
            return render(request, self.template_name, self.get_context(obj))
        return super().post(request, *args, **kwargs)

    def perform(self, obj):
        try:
            obj.delete()
        except ProtectedError as exc:
            # The race the check above cannot close: a roster created between
            # the count and the delete. PROTECT is what actually holds the
            # line, so this only has to turn it into a sentence.
            raise ValidationError(
                _("%(season)s has a roster in it now, so it cannot be deleted.")
                % {"season": obj.label}
            ) from exc

    def get_success_url(self, obj):
        return reverse("fantasy:season-list")


# --- manager profiles --------------------------------------------------------


class OwnManagerMixin(LoginRequiredMixin):
    """Scopes every manager route to the account behind it.

    Looked up through `request.user.manager_profiles` rather than
    `Manager.objects`, so someone else's profile is not in the set being
    searched at all -- a 404 by construction rather than by an `if` a later
    view can forget. The same shape as `OwnRosterMixin`, one level up.
    """

    def get_manager(self):
        if not hasattr(self, "_manager"):
            self._manager = get_object_or_404(
                self.request.user.manager_profiles, pk=self.kwargs["pk"]
            )
        return self._manager


class ManagerListView(LoginRequiredMixin, ListView):
    """Every profile on the account, with the rosters played under each.

    The rosters are shown rather than counted because they are the reason a
    profile cannot always be deleted: seeing them is seeing why.
    """

    template_name = "fantasy/manager_list.html"
    context_object_name = "profiles"

    def get_queryset(self):
        return self.request.user.manager_profiles.prefetch_related(
            # A Prefetch rather than a plain string, so the season comes with
            # each roster and the order is the one the roster list uses.
            Prefetch(
                "rosters",
                queryset=Roster.objects.select_related("season")
                .prefetch_related(
                    Prefetch(
                        "memberships",
                        queryset=RosterPlayer.objects.open()
                        .select_related("player", "player__team")
                        .order_by("player__last_name", "player__first_name"),
                        to_attr="open_memberships",
                    )
                )
                .order_by("-season__starts_on", "name"),
            )
        )


class ManagerDetailView(StaffRequiredMixin, DetailView):
    """A staff-only look at somebody else's profile: what it plays, read-only.

    Not `OwnManagerMixin` -- the point here is precisely to look at a profile
    that is not yours. Rosters are prefetched the same way `ManagerListView`
    prefetches its own, open memberships included, so the season groups render
    the same summary either page shows.
    """

    model = Manager
    template_name = "fantasy/manager_detail.html"
    context_object_name = "profile"

    def get_queryset(self):
        return Manager.objects.select_related("user").prefetch_related(
            Prefetch(
                "rosters",
                queryset=Roster.objects.select_related("season")
                .prefetch_related(
                    Prefetch(
                        "memberships",
                        queryset=RosterPlayer.objects.open()
                        .select_related("player", "player__team")
                        .order_by("player__last_name", "player__first_name"),
                        to_attr="open_memberships",
                    )
                )
                .order_by("-season__starts_on", "name"),
            )
        )


class ManagerNamePromptView(PromptView):
    """The half the create and rename prompts share: one nickname, checked.

    Both ask the same question of the same field, and both have to answer the
    same objection -- that the account already uses that handle. Split so the
    rule lives once; a rename excludes itself from the check, which is what
    lets someone save the dialog without renaming anything.
    """

    field_label = _lazy("Nickname")
    field_name = "nick_name"
    placeholder = "e.g. Buzz"
    # `Manager.nick_name` is 40, and a longer value would be truncated by the
    # database rather than by anyone's intention.
    max_length = 40
    help_text = ""

    def clean(self, value, obj, choice=None):
        value = super().clean(value, obj, choice)
        taken = self.request.user.manager_profiles.filter(nick_name__iexact=value)
        if obj is not None:
            taken = taken.exclude(pk=obj.pk)
        if taken.exists():
            # `iexact`, which is stricter than `unique_nickname_per_user` --
            # that constraint compares exactly. Deliberate: the constraint
            # exists because two profiles by one name cannot be told apart on
            # screen, and "Buzz" beside "buzz" is that problem exactly. The
            # constraint stays the backstop for an exact duplicate racing in.
            raise ValidationError(
                _('You already have a profile called "%(name)s".') % {"name": value}
            )
        return value

    def get_success_url(self, result):
        return reverse("fantasy:manager-list")


class ManagerCreateView(ManagerNamePromptView):
    title = _lazy("New manager profile")
    body = ""
    submit_label = _lazy("Create profile")

    def initial_value(self, obj):
        # Blank on purpose. `default_nickname` suggests a handle from the
        # account, which is right for the profile that appears with a first
        # roster -- but by the time anyone opens this dialog that profile
        # usually exists, so the suggestion would arrive already taken.
        return ""

    def perform(self, obj, value, choice=None):
        return Manager.objects.create(user=self.request.user, nick_name=value)


class ManagerRenameView(OwnManagerMixin, ManagerNamePromptView):
    """A rename, not a new handle: the rosters underneath do not move."""

    title = _lazy("Rename profile")
    submit_label = _lazy("Save")

    def get_object(self):
        return self.get_manager()

    def initial_value(self, obj):
        return obj.nick_name

    def perform(self, obj, value, choice=None):
        obj.nick_name = value
        obj.save(update_fields=["nick_name", "updated_at"])
        return obj


class ManagerDeleteView(OwnManagerMixin, TypedConfirmView):
    """Deletes a profile -- but only one with nothing under it.

    `Roster.manager` cascades, so deleting a profile that still plays rosters
    would take them and their whole transaction history with it. "Delete this
    handle" and "destroy a season of roster history" are not the same
    intention, so this refuses rather than warning: the rosters go first, by
    the dialog that is honest about what deleting a roster costs.
    """

    title = _lazy("Delete this profile?")
    # No word to type. A profile that may be deleted holds nothing, so typing
    # DELETE would be friction guarding nothing -- and friction that guards
    # nothing teaches people to type it without reading. The word earns its
    # place on the roster dialog, where the loss is real.
    confirm_word = ""
    confirm_label = _lazy("Delete profile")

    def get_object(self):
        return self.get_manager()

    def held_rosters(self, obj):
        if not hasattr(self, "_held"):
            self._held = list(obj.rosters.order_by("name"))
        return self._held

    def get_context(self, obj, error=None):
        context = super().get_context(obj, error=error)
        if self.held_rosters(obj):
            # No confirm button at all, rather than one that refuses when
            # pressed: the modal is an explanation, not a dare.
            context["confirm_label"] = ""
            context["cancel_label"] = _("Close")
        return context

    def get_body(self, obj):
        held = self.held_rosters(obj)
        if held:
            names = ", ".join(f'"{roster.name}"' for roster in held)
            return _(
                '"%(name)s" still plays %(rosters)s. Delete or rename those first -- '
                "removing the profile would take them and their whole transaction "
                "history with it."
            ) % {"name": obj.nick_name, "rosters": names}
        return _(
            '"%(name)s" plays no rosters, so nothing else goes with it. '
            "The handle is gone for good, though."
        ) % {"name": obj.nick_name}

    def post(self, request, *args, **kwargs):
        obj = self.get_object()
        if self.held_rosters(obj):
            # Reachable without the button: a hand-written POST, or a dialog
            # that was open when a roster was created under this profile.
            # Re-rendered rather than a 403 -- the modal already says why.
            return render(request, self.template_name, self.get_context(obj))
        return super().post(request, *args, **kwargs)

    def perform(self, obj):
        obj.delete()

    def get_success_url(self, obj):
        return reverse("fantasy:manager-list")


# --- rosters -----------------------------------------------------------------


class OwnRosterMixin(LoginRequiredMixin):
    """Scopes every roster route to the account behind it.

    Someone else's roster UUID gets a 404 rather than a look, and the check is
    in the queryset rather than an `if` a later view can forget.

    Matched through the manager to the user, not to the manager itself: a roster
    under any of your own profiles is yours. Scoping to one profile would make
    the answer depend on which one you were viewing as, and there is no such
    notion in the app.
    """

    def get_roster(self):
        if not hasattr(self, "_roster"):
            self._roster = get_object_or_404(
                Roster.objects.select_related("season", "manager"),
                pk=self.kwargs["pk"],
                manager__user=self.request.user,
            )
            # Free trades that fell due since the roster was last looked at.
            services.pay_due_trade_grants([self._roster])
        return self._roster


class RosterListView(LoginRequiredMixin, ListView):
    template_name = "fantasy/roster_list.html"
    context_object_name = "rosters"

    def get_queryset(self):
        # Your own rosters only, staff included: every roster page is scoped to
        # its owner (`OwnRosterMixin`), so listing anyone else's would only
        # offer links that 404. Staff see other managers' rosters on Managers.
        return (
            Roster.objects.select_related("season", "manager")
            .with_squad()
            .filter(manager__user=self.request.user)
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        services.pay_due_trade_grants(context["rosters"])
        context["current_season"] = Season.objects.filter(is_current=True).first()
        return context


class RosterCreateView(PromptView):
    """Asks for a name and a manager, then creates the roster.

    The manager is only asked for when there is something to decide -- see
    `get_choices`. The rules screen comes next either way.
    """

    title = _lazy("New roster")
    field_label = _lazy("Roster name")
    field_name = "name"
    placeholder = _lazy("e.g. Bulla Ballers")
    max_length = 80
    help_text = _lazy("You can rename it at any time.")
    submit_label = _lazy("Create roster")
    choice_name = "manager"
    choice_label = _lazy("Manager")
    choice_help = _lazy("Which of your profiles plays this roster.")
    template_name = "fantasy/partials/roster_create.html"

    def dispatch(self, request, *args, **kwargs):
        # A roster always belongs to a season, so there is nothing to ask for
        # until one is marked current.
        self.season = Season.objects.filter(is_current=True).first()
        if self.season is None and request.user.is_authenticated:
            return render(request, "fantasy/no_season.html", status=409)
        if request.user.is_authenticated and not request.user.manager_profiles.exists():
            return render(request, "fantasy/partials/roster_no_profile.html")
        return super().dispatch(request, *args, **kwargs)

    def initial_value(self, obj):
        # Prefilled, so the flow stays one keystroke long for anyone who does
        # not care what their roster is called. Suggested for the profile the
        # select starts on; switching the select does not re-suggest, and does
        # not need to -- `clean` checks the name against whichever was chosen.
        #
        # `manager_for`, not `ensure_manager`: this runs on the GET that opens
        # the dialog, and cancelling out of it must not leave a profile behind.
        manager = services.manager_for(self.request.user)
        return services.default_roster_name(manager, self.season)

    def profiles(self):
        """This account's manager profiles, by name, cached for the request."""
        if not hasattr(self, "_profiles"):
            self._profiles = list(self.request.user.manager_profiles.order_by("nick_name"))
        return self._profiles

    def get_choices(self):
        """The profiles to offer, or nothing when there is no decision to make.

        One profile is not a choice, and none means this is a first roster that
        will bring a profile with it -- in both cases a select would be a
        control with a single answer, so the modal stays one field.
        """
        profiles = self.profiles()
        if len(profiles) < 2:
            return []
        return [(str(manager.pk), manager.nick_name) for manager in profiles]

    def get_context(self, value, error=None, choice_value=None):
        context = super().get_context(value, error=error, choice_value=choice_value)
        context["icon_choices"] = ROSTER_ICON_CHOICES
        context["icon_value"] = (
            self.request.POST.get("icon", "koala") if self.request.method == "POST" else "koala"
        )
        return context

    def default_choice(self):
        """The same profile a roster would have landed under before the picker.

        Listed by name but preselected by age, so the order is readable while
        the default stays the one the rest of the app already treats as yours.
        """
        manager = services.manager_for(self.request.user)
        return str(manager.pk) if manager is not None else ""

    def clean_choice(self, raw):
        """Resolve the submitted profile, or None to let `perform` make one.

        Looked up through `manager_profiles` rather than `Manager.objects`, so
        another account's UUID cannot be posted in: it simply is not in the set
        being searched. That is the whole check, and it is here rather than in
        the template because the rendered options bind the browser and nothing
        else.
        """
        if not raw:
            # No select was rendered, or an old form was posted without one.
            return services.manager_for(self.request.user)
        try:
            return self.request.user.manager_profiles.get(pk=raw)
        except (Manager.DoesNotExist, ValidationError, ValueError, TypeError):
            # DoesNotExist covers somebody else's profile; the rest cover a
            # value that is not a UUID at all.
            raise ValidationError(_("Pick one of your manager profiles.")) from None

    def clean(self, value, obj, choice=None):
        value = super().clean(value, obj, choice)
        # Checked here rather than left to the unique constraint, so the user
        # gets a sentence instead of an IntegrityError. Scoped to the profile
        # the roster will actually land under, which is what the constraint
        # covers -- a name is free if that profile has not used it.
        taken = (
            choice is not None
            and Roster.objects.filter(manager=choice, season=self.season, name=value).exists()
        )
        if taken:
            # Named only when the user was offered the choice. "You already
            # have" is the truth when there is one profile; naming it then
            # would point at a decision they were never asked to make.
            if self.get_choices():
                raise ValidationError(
                    _('%(manager)s already has a roster called "%(name)s" this season.')
                    % {"manager": choice.nick_name, "name": value}
                )
            raise ValidationError(
                _('You already have a roster called "%(name)s" this season.') % {"name": value}
            )
        return value

    def perform(self, obj, value, choice=None):
        # `choice` is None only for a first roster, and this is the one place a
        # profile is created: past validation, with a roster about to exist for
        # it to manage.
        manager = choice or services.ensure_manager(self.request.user)
        # Through the service, not `Roster.objects.create`, so the signings
        # cutoff refuses here too. The Rosters page hides its button once the
        # season has closed; this is what answers a hand-written request.
        icon = self.clean_icon(self.request.POST.get("icon", ""))
        return services.create_roster(manager, self.season, value, icon)

    def clean_icon(self, raw):
        choices = dict(ROSTER_ICON_CHOICES)
        # Older clients and direct POSTs may omit the new field; the modal
        # always submits its default selection, and legacy callers keep the
        # first available icon rather than breaking roster creation.
        raw = raw or "koala"
        if raw not in choices:
            raise ValidationError(_("Pick one of the available roster icons."))
        return raw

    def get_success_url(self, result):
        return reverse("fantasy:roster-rules", args=[result.pk])


class RosterRulesView(OwnRosterMixin, TemplateView):
    template_name = "fantasy/roster_rules.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Straight from the constants the completeness check reads, so this
        # screen cannot promise rules the code does not enforce.
        context["roster"] = self.get_roster()
        context["roster_size"] = ROSTER_SIZE
        context["minimums"] = [
            {"label": Player.Position(position).label, "minimum": minimum}
            for position, minimum in MINIMUM_BY_POSITION.items()
        ]
        context["required_slots"] = sum(MINIMUM_BY_POSITION.values())
        context["flex_slots"] = ROSTER_SIZE - context["required_slots"]
        season = context["roster"].season
        # The trade calendar: one-off grants by date, the weekly ones as a rule.
        # Past grants are marked received and the next one highlighted, so the
        # calendar answers "when is my next trade" at a glance.
        now = timezone.now()
        grants = list(season.trade_grants.all())
        upcoming = [grant for grant in grants if grant.granted_at > now]
        for grant in grants:
            grant.is_received = grant.granted_at <= now
            grant.is_next = bool(upcoming) and grant is upcoming[0]
        context["trade_grants"] = grants
        context["next_trade_grant"] = services.next_trade_grant(season, now)
        context["trade_buy_price"] = TRADE_BUY_PRICE
        context["trade_sell_price"] = TRADE_SELL_PRICE
        return context


class RosterHistoryView(OwnRosterMixin, TemplateView):
    """Every recorded move, newest first, with the balance it left behind.

    The running balance is the point of the screen rather than decoration. It is
    computed forward from the roster's starting budget through the whole log, so the final
    figure either agrees with `Roster.cash` or the two have drifted apart -- and
    `reconciles` says which, on the page, where it can be noticed. A ledger
    nobody checks is not a ledger.
    """

    template_name = "fantasy/roster_history.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        roster = self.get_roster()

        # Chronological for the arithmetic, reversed afterwards for the screen.
        # The tie-break on created_at matters: seeded rosters record several
        # moves for the same instant, and a running balance needs a total order.
        moves = list(
            roster.transactions.select_related(
                "player_in", "player_in__team", "player_out", "player_out__team"
            ).order_by("occurred_at", "created_at")
        )

        balance = roster.starting_cash
        rows = []
        for move in moves:
            balance += move.cash_delta
            rows.append({"move": move, "balance": balance})

        context["roster"] = roster
        context["rows"] = list(reversed(rows))
        context["starting_cash"] = roster.starting_cash
        context["ledger_balance"] = balance
        context["reconciles"] = balance == roster.cash
        context["counts"] = Counter(move.kind for move in moves)
        # Rows that predate per-side prices, counted so the page can admit to
        # them instead of quietly showing a blank column.
        context["unpriced"] = sum(1 for move in moves if not move.is_priced)
        return context


def _picker_filters(request):
    """Filter values, wherever they arrived from.

    Buy and sell are POSTs that hx-include the filter form, so the refreshed
    player list keeps the user's search instead of resetting it.
    """
    data = request.POST if request.method == "POST" else request.GET
    return {
        "q": data.get("q", "").strip(),
        "position": data.get("position", ""),
        "team": data.get("team", "").strip(),
    }


def _available_players(roster, filters, budget=None):
    """The players this roster could still add, marked by affordability.

    `budget` is what the marking is done against, and it is not always the
    balance: a trade is judged against the cash plus whatever the outgoing
    player frees up, which is usually far more than is in hand.
    """
    budget = roster.cash if budget is None else budget
    qs = (
        Player.objects.filter(is_active=True, current_salary__isnull=False)
        .exclude(
            # Already on the roster: an open membership row.
            roster_memberships__roster=roster,
            roster_memberships__removed_at__isnull=True,
        )
        .select_related("team")
        .annotate(
            # Django templates cannot compare with <=, so affordability is
            # decided in SQL. A NULL salary was excluded above.
            affordable=ExpressionWrapper(Q(current_salary__lte=budget), output_field=BooleanField())
        )
        .order_by("-current_salary", "last_name")
    )

    for term in filters["q"].split():
        qs = qs.filter(Q(first_name__icontains=term) | Q(last_name__icontains=term))
    if filters["position"] in Player.Position.values:
        qs = qs.filter(position=filters["position"])
    if filters["team"]:
        qs = qs.filter(team__abbreviation=filters["team"])
    return qs


SQUAD_SORT_OPTIONS = [
    ("name", _lazy("Player")),
    ("position", _lazy("Position")),
    ("team", _lazy("Team")),
    ("salary", _lazy("Actual salary")),
    ("difference", _lazy("Value")),
    ("avg", _lazy("Avg/game")),
    ("games", _lazy("Games")),
    ("hotness", _lazy("Hotness")),
]

ROSTER_SORT_FIELDS = {
    "name": lambda player: (player.last_name, player.first_name),
    "position": lambda player: player.position,
    "team": lambda player: player.team.name if player.team else None,
    "salary": lambda player: player.current_salary,
    "expected": lambda player: player.predicted_salary,
    "difference": lambda player: player.salary_difference_amount,
    "points": lambda player: player.current_total_fp,
    "avg": lambda player: player.current_fp_per_game,
    "games": lambda player: player.current_games_played,
    "hotness": lambda player: _hotness_value(player.hotness_score()),
}


def _hotness_value(score):
    if not score:
        return None
    increases, comparisons = (int(value) for value in score.split("/"))
    return (Decimal(increases) / Decimal(comparisons), comparisons)


def _sort_roster_memberships(request, memberships):
    sort = request.GET.get("sort", "name")
    if sort not in ROSTER_SORT_FIELDS:
        sort = "name"
    direction = request.GET.get("dir")
    descending = sort in DESCENDING_FIRST if direction is None else direction == "desc"
    key = ROSTER_SORT_FIELDS[sort]
    present = [membership for membership in memberships if key(membership.player) is not None]
    missing = [membership for membership in memberships if key(membership.player) is None]
    present.sort(key=lambda membership: key(membership.player), reverse=descending)
    return sort, ("desc" if descending else "asc"), present + missing


def _signings_deadline(season, now=None):
    """The next signing-window moment worth a line on the build page.

    Before signings open: when they open. While they are open and a cutoff is
    set: when they close, flagged once less than a day is left. Afterwards
    nothing -- the panel already says signings are closed.
    """
    now = now or timezone.now()
    if season.signings_open_at and now < season.signings_open_at:
        return {"signings_open_at_upcoming": season.signings_open_at}
    if season.transactions_allowed and season.signings_close_at and now < season.signings_close_at:
        return {
            "signings_deadline": season.signings_close_at,
            "signings_deadline_soon": season.signings_close_at - now < dt.timedelta(hours=24),
        }
    return {}


def _build_context(request, roster):
    filters = _picker_filters(request)
    # Read off the roster rather than queried here, so this list and every
    # figure derived from it -- the size, the value, the shortfalls, and
    # `is_complete` once per row of the market below -- all come from the one
    # load. See `Roster.current_memberships`.
    memberships = roster.current_memberships
    for membership in memberships:
        player = membership.player
        if player.predicted_salary is not None and player.current_salary is not None:
            player.salary_difference_amount = (
                player.predicted_salary * Decimal("1000000") - player.current_salary
            )
        else:
            player.salary_difference_amount = None
    current_sort, current_dir, memberships = _sort_roster_memberships(request, memberships)
    trading_allowed = roster.season.trading_allowed
    if not trading_allowed:
        now = timezone.now()
        if now < roster.season.season_open_at:
            trade_unavailable_reason = _(
                "Trades become available when the season starts on %(date)s."
            ) % {"date": local_date(roster.season.season_open_at)}
        else:
            trade_unavailable_reason = _("The season has ended. Trading is closed.")
    else:
        trade_unavailable_reason = ""

    # Buying and selling trades has its own window inside the trading period.
    market_closed_reason = (
        trade_unavailable_reason or services.trade_market_closed_reason(roster.season)
    )
    can_buy_trade = not market_closed_reason and roster.cash >= TRADE_BUY_PRICE
    if market_closed_reason:
        buy_trade_disabled_reason = market_closed_reason
    elif roster.cash < TRADE_BUY_PRICE:
        buy_trade_disabled_reason = _("Not enough cash (%(price)s needed)") % {
            "price": millions(TRADE_BUY_PRICE)
        }
    else:
        buy_trade_disabled_reason = ""

    can_sell_trade = not market_closed_reason and roster.trades_available > 0
    if market_closed_reason:
        sell_trade_disabled_reason = market_closed_reason
    elif roster.trades_available <= 0:
        sell_trade_disabled_reason = _("No trades available to sell")
    else:
        sell_trade_disabled_reason = ""

    return {
        "roster": roster,
        "filters": filters,
        "available": _available_players(roster, filters)[:100],
        "memberships": memberships,
        "current_sort": current_sort,
        "current_dir": current_dir,
        # The phone cards' "Sort by" list: the table's columns, in its order.
        "squad_sort_options": SQUAD_SORT_OPTIONS,
        "positions": Player.Position.choices,
        "teams": Team.objects.order_by("name"),
        "roster_size": ROSTER_SIZE,
        "progress": roster.position_progress(),
        # The strip pinned above the market: positions against their minimums,
        # and what an average open slot may still cost.
        "squad": roster.position_progress(),
        "open_slots": max(0, ROSTER_SIZE - roster.player_count),
        "cash_per_open_slot": (
            roster.cash / (ROSTER_SIZE - roster.player_count)
            if roster.player_count < ROSTER_SIZE
            else None
        ),
        # What the picker and the squad panel branch on. `roster.season` is
        # select_related by `OwnRosterMixin`, so this costs nothing.
        "signings_open": roster.season.transactions_allowed,
        "transactions_allowed": roster.season.transactions_allowed,
        "trading_allowed": trading_allowed,
        "trades_available": roster.trades_available,
        "has_trades": roster.trades_available > 0,
        "can_buy_trade": can_buy_trade,
        "can_sell_trade": can_sell_trade,
        "buy_trade_disabled_reason": buy_trade_disabled_reason,
        "sell_trade_disabled_reason": sell_trade_disabled_reason,
        "trade_unavailable_reason": trade_unavailable_reason,
        # Shown under the Buy/Sell buttons: why the trade market is shut.
        "trade_market_note": market_closed_reason,
        "signings_close_at": roster.season.signings_close_at,
        **_signings_deadline(roster.season),
        "next_trade_grant": services.next_trade_grant(roster.season),
    }


class RosterBuildView(OwnRosterMixin, TemplateView):
    def get_template_names(self):
        # A filter change only needs the picker back, not the team card above.
        if self.request.htmx:
            return ["fantasy/partials/player_picker.html"]
        return ["fantasy/roster_build.html"]

    def get_context_data(self, **kwargs):
        return _build_context(self.request, self.get_roster())


class RosterChangeView(OwnRosterMixin, View):
    """Shared plumbing for buy and sell.

    Both change the roster panel *and* the player list -- the bought player
    leaves it, and a lower balance makes more of the rest unaffordable -- so
    both are returned, the picker out of band.
    """

    template_name = "fantasy/partials/build_update.html"

    def apply(self, roster, player):
        """Make the change; return the sentence to confirm it with."""
        raise NotImplementedError

    def post(self, request, *args, **kwargs):
        roster = self.get_roster()
        player = get_object_or_404(Player, pk=kwargs["player_pk"])

        # Success and failure both come back as a toast: the buttons are far
        # down the market, where a message at the top of the page goes unseen.
        try:
            message, level = self.apply(roster, player), "success"
        except ValidationError as exc:
            message, level = exc.messages[0], "error"

        roster.refresh_from_db()
        response = render(request, self.template_name, _build_context(request, roster))
        return add_toast(response, message, level)


class RosterBuyView(RosterChangeView):
    def apply(self, roster, player):
        if player.current_salary is None:
            raise ValidationError(_("No salary on record for %(player)s.") % {"player": player})
        # The price is read here, never taken from the request: a client that
        # could name its own price could sign anyone for a euro.
        services.buy(roster, player, player.current_salary)
        return _("%(name)s signed for %(price)s.") % {
            "name": player.full_name,
            "price": millions(player.current_salary),
        }


class RosterSellView(RosterChangeView):
    def apply(self, roster, player):
        refund = services.amount_paid_for(roster, player)
        services.sell(roster, player, refund)
        return _("%(name)s released, %(price)s back.") % {
            "name": player.full_name,
            "price": millions(refund),
        }


def player_roster_options(user, player):
    """What each of `user`'s current-season rosters can do with `player`.

    For the "Your rosters" box on a player page. While signings are open a
    roster can sign him (unless full, short of cash, or he has no price);
    after the deadline it can trade him in through the usual dialog.
    """
    season = Season.objects.filter(is_current=True).first()
    if season is None:
        return []
    rosters = list(
        Roster.objects.filter(manager__user=user, season=season)
        .select_related("season", "manager")
        .with_squad()
        .order_by("name")
    )
    services.pay_due_trade_grants(rosters)
    options = []
    for roster in rosters:
        option = {
            "roster": roster,
            "roster_size": ROSTER_SIZE,
            "on_roster": player in roster.current_squad,
        }
        if option["on_roster"]:
            pass
        elif season.transactions_allowed:
            option["action"] = "sign"
            if player.current_salary is None:
                option["blocked"] = _("No salary on record yet.")
            elif roster.player_count >= ROSTER_SIZE:
                option["blocked"] = _("Roster is full.")
            elif player.current_salary > roster.cash:
                option["blocked"] = _("Not enough cash (%(cash)s left).") % {
                    "cash": millions(roster.cash)
                }
        elif season.trading_allowed:
            option["action"] = "trade"
        options.append(option)
    return options


def render_player_roster_options(request, player):
    return render(
        request,
        "fantasy/partials/player_roster_options.html",
        {"player": player, "roster_options": player_roster_options(request.user, player)},
    )


class PlayerSignView(OwnRosterMixin, View):
    """Sign a player from his own page, into one of your rosters.

    The same rules and the same server-side price as signing from the market;
    only the response differs: the player page's roster box, plus a toast.
    """

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        roster = self.get_roster()
        player = get_object_or_404(Player, pk=kwargs["player_pk"])
        try:
            message = RosterBuyView().apply(roster, player)
            level = "success"
        except ValidationError as exc:
            message, level = exc.messages[0], "error"
        message = (
            _("%(message)s Roster: %(roster)s.") % {"message": message, "roster": roster.name}
            if level == "success"
            else message
        )
        return add_toast(render_player_roster_options(request, player), message, level)


class RosterBuyTradeView(OwnRosterMixin, ConfirmView):
    title = _lazy("Buy an available trade?")
    confirm_label = _lazy("Buy Trade ($1.5M)")
    tone = "primary"

    def get_object(self):
        return self.get_roster()

    def get_body(self, roster):
        return _(
            "Do you really want to buy 1 extra trade for %(price)s cash? "
            "Your cash balance will decrease from %(before)s to %(after)s."
        ) % {
            "price": millions(TRADE_BUY_PRICE),
            "before": millions(roster.cash),
            "after": millions(roster.cash - TRADE_BUY_PRICE),
        }

    def perform(self, roster):
        services.buy_trade(roster)
        roster.refresh_from_db()
        message = _("Trade bought for %(price)s.") % {"price": millions(TRADE_BUY_PRICE)}
        response = render(
            self.request,
            "fantasy/partials/build_update.html",
            _build_context(self.request, roster),
        )
        response["HX-Retarget"] = "#roster-panel"
        response["HX-Reswap"] = "outerHTML"
        return add_toast(response, message)


class RosterSellTradeView(OwnRosterMixin, ConfirmView):
    title = _lazy("Sell an available trade?")
    confirm_label = _lazy("Sell Trade (+$1.0M)")
    tone = "primary"

    def get_object(self):
        return self.get_roster()

    def get_body(self, roster):
        return _(
            "Do you really want to sell 1 trade for %(price)s cash? "
            "Your available trades will decrease from %(trades)d to %(trades_after)d, "
            "and your cash balance will increase from %(before)s to %(after)s."
        ) % {
            "price": millions(TRADE_SELL_PRICE),
            "trades": roster.trades_available,
            "trades_after": roster.trades_available - 1,
            "before": millions(roster.cash),
            "after": millions(roster.cash + TRADE_SELL_PRICE),
        }

    def perform(self, roster):
        services.sell_trade(roster)
        roster.refresh_from_db()
        message = _("Trade sold for %(price)s.") % {"price": millions(TRADE_SELL_PRICE)}
        response = render(
            self.request,
            "fantasy/partials/build_update.html",
            _build_context(self.request, roster),
        )
        response["HX-Retarget"] = "#roster-panel"
        response["HX-Reswap"] = "outerHTML"
        return add_toast(response, message)


def _flag_minimum_breaks(roster, player_out, candidates):
    """Mark each candidate whose arrival for `player_out` leaves a position short.

    Sets `trade_leaves` to e.g. "4 G" on those, None on the rest. Only a
    position the swap itself takes below its minimum counts: a roster already
    short of a center is not flagged again for every trade that leaves it so.
    """
    counts = roster.position_counts()
    for candidate in candidates:
        after = dict(counts)
        after[player_out.position] -= 1
        after[candidate.position] += 1
        candidate.trade_leaves = next(
            (
                f"{after[position]} {position}"
                for position, minimum in MINIMUM_BY_POSITION.items()
                if after[position] < minimum and after[position] < counts[position]
            ),
            None,
        )


def _chosen_player(request, key):
    """The player named by `key` in the query string, or None.

    A stale or malformed id is an unmade choice rather than a 404, because these
    ids come out of a URL the trade modal rewrites on every click -- and a
    player sold in another tab should leave the screen, not break it.
    """
    raw = request.GET.get(key) or request.POST.get(key) or ""
    if not raw:
        return None
    try:
        return Player.objects.select_related("team").filter(pk=raw).first()
    except (ValidationError, ValueError):
        return None


class RosterTradeView(OwnRosterMixin, View):
    """One player out, one player in, one transaction.

    Both sides are priced at today's salary and the roster keeps the difference
    -- see `services.trade_preview` for why that, and not what was paid.

    The half-made choice lives in the query string rather than in the session,
    so the modal has no state of its own to get out of step with the roster:
    every click is a new URL that renders from the database. `?body=1` marks the
    requests that come from inside the modal and only need its body back, which
    is what keeps the Alpine shell -- and the open animation -- from restarting
    on every selection.
    """

    template_name = "fantasy/trade_modal.html"
    body_template = "fantasy/partials/trade_body.html"

    def get_context(self, error=None):
        request = self.request
        roster = self.get_roster()

        # The same load the preview below counts positions from, so opening
        # the modal reads the squad once rather than once per figure.
        memberships = roster.current_memberships
        on_roster = {membership.player_id for membership in memberships}

        # Both sides are checked against the roster as it is right now, so the
        # modal can never offer a trade the service layer would refuse.
        player_out = _chosen_player(request, "out")
        if player_out is not None and player_out.pk not in on_roster:
            player_out = None
        player_in = _chosen_player(request, "in")
        if player_in is not None and player_in.pk in on_roster:
            player_in = None

        preview = services.trade_preview(roster, player_out, player_in)
        filters = _picker_filters(request)
        available = _available_players(roster, filters, budget=preview["budget"])[:60]
        # Carried through every link inside the dialog, so a trade started on a
        # player page still knows where to go when it is executed.
        return_query = "&return_to=player" if request.GET.get("return_to") == "player" else ""
        if player_out is not None:
            _flag_minimum_breaks(roster, player_out, available)

        return {
            "roster": roster,
            "memberships": memberships,
            "filters": filters,
            "positions": Player.Position.choices,
            "available": available,
            # Where to go after a trade made from somewhere other than the
            # build page (the player page), instead of swapping in its panels.
            "return_to": "player" if request.GET.get("return_to") == "player" else "",
            # Phones show one list at a time: who goes out (1), then who comes in (2).
            "mobile_step": 2 if player_out is not None else 1,
            "preview": preview,
            "trade_url": reverse("fantasy:roster-trade", args=[roster.pk]),
            # Ready-made query fragments, so a row that changes one side of the
            # trade does not drop the other.
            "keep_out": (f"&out={player_out.pk}" if player_out else "") + return_query,
            "keep_in": (f"&in={player_in.pk}" if player_in else "") + return_query,
            "state_query": "?body=1"
            + (f"&out={player_out.pk}" if player_out else "")
            + (f"&in={player_in.pk}" if player_in else "")
            + return_query,
            "panel_class": "modal-panel-xl",
            "error": error,
            # What to tell a roster with no trades left: buy one now, or when
            # the next free one arrives.
            "can_buy_trade": roster.season.trade_market_open and roster.cash >= TRADE_BUY_PRICE,
            "trade_buy_price": TRADE_BUY_PRICE,
            "next_trade_grant": services.next_trade_grant(roster.season),
        }

    def get(self, request, *args, **kwargs):
        context = self.get_context()
        template = self.body_template if request.GET.get("body") else self.template_name
        return render(request, template, context)

    def post(self, request, *args, **kwargs):
        roster = self.get_roster()
        preview = self.get_context()["preview"]

        error = None
        if not preview["has_trades"]:
            error = _("No trades available for this roster.")
        elif not preview["ready"]:
            error = _("Pick a player to send out and one to bring in.")
        else:
            try:
                self.execute(roster, preview)
            except ValidationError as exc:
                error = exc.messages[0]

        if error:
            # The confirm button aims at the roster panel, because that is where
            # a trade that goes through lands. One that does not keeps the modal
            # open instead, with both choices still made and the reason on it.
            response = render(request, self.body_template, self.get_context(error=error))
            response["HX-Retarget"] = "#trade-body"
            response["HX-Reswap"] = "outerHTML"
            return response

        done = _("Trade done: %(out)s out, %(in)s in.") % {
            "out": preview["player_out"].full_name,
            "in": preview["player_in"].full_name,
        }
        if request.GET.get("return_to") == "player":
            # Started on a player page, which has no roster panel to update:
            # go to the roster instead, and say what happened there.
            messages.success(request, done)
            response = HttpResponse(status=204)
            response["HX-Redirect"] = reverse("fantasy:roster-build", args=[roster.pk])
            return response

        roster.refresh_from_db()
        response = render(
            request, "fantasy/partials/build_update.html", _build_context(request, roster)
        )
        response["HX-Trigger"] = "close-modal"
        return add_toast(response, done)

    def execute(self, roster, preview):
        player_out, player_in = preview["player_out"], preview["player_in"]
        for player in (player_out, player_in):
            if player.current_salary is None:
                raise ValidationError(
                    _("No salary on record for %(player)s.") % {"player": player}
                )
        # The prices are read off the players here, never taken from the
        # request: a client that could name both prices could trade anyone for
        # anyone and bank the difference.
        services.trade(
            roster,
            player_out,
            player_in,
            price_out=player_out.current_salary,
            price_in=player_in.current_salary,
        )


class RosterRenameView(OwnRosterMixin, View):
    def post(self, request, *args, **kwargs):
        roster = self.get_roster()
        name = request.POST.get("name", "").strip()
        if name and name != roster.name:
            roster.name = name[:80]
            roster.save(update_fields=["name", "updated_at"])
        return render(request, "fantasy/partials/roster_name.html", {"roster": roster})


class RosterDeleteView(OwnRosterMixin, TypedConfirmView):
    title = _lazy("Delete this roster?")
    confirm_label = _lazy("Delete permanently")

    def get_object(self):
        return self.get_roster()

    def get_body(self, obj):
        return ngettext(
            '"%(name)s" will be deleted along with its %(count)d player and its '
            "entire transaction history. This cannot be undone.",
            '"%(name)s" will be deleted along with its %(count)d players and its '
            "entire transaction history. This cannot be undone.",
            obj.player_count,
        ) % {"name": obj.name, "count": obj.player_count}

    def perform(self, obj):
        obj.delete()

    def get_success_url(self, obj):
        return reverse("fantasy:roster-list")
