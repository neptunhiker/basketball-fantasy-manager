from collections import Counter
from decimal import Decimal

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
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views import View
from django.views.generic import ListView, TemplateView

from apps.core.views import (
    AdminRequiredMixin,
    ConfirmView,
    ModalFormView,
    PromptView,
    TypedConfirmView,
)
from apps.nba.models import Player, Team

from . import services
from .forms import SeasonForm
from .models import (
    MINIMUM_BY_POSITION,
    ROSTER_SIZE,
    ROSTER_ICON_CHOICES,
    STARTING_CASH,
    Manager,
    PlayerSnapshot,
    Roster,
    RosterPlayer,
    Season,
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
    title = "New season"
    body = (
        "A season is the frame everything else sits in: rosters belong to one, "
        "and so does every imported price."
    )
    submit_label = "Create season"


class SeasonUpdateView(SeasonFormView):
    title = "Edit season"
    submit_label = "Save changes"

    def get_object(self):
        if not hasattr(self, "_season"):
            self._season = get_object_or_404(Season, pk=self.kwargs["pk"])
        return self._season


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

    title = "Delete this season?"
    confirm_label = "Delete season"

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
        rosters, _ = self.counts(obj)
        if rosters:
            # No confirm button at all rather than one that fails: the modal is
            # explaining why this cannot be done.
            context["confirm_label"] = ""
            context["cancel_label"] = "Close"
        return context

    def get_body(self, obj):
        rosters, snapshots = self.counts(obj)
        if rosters:
            return (
                f"{obj.label} still holds {rosters} "
                f"{'roster' if rosters == 1 else 'rosters'}. Delete those first -- "
                "a roster's whole transaction history hangs off its season, and "
                "the database will not let the season go while they exist."
            )
        if snapshots:
            return (
                f"{obj.label} holds {snapshots:,} imported "
                f"{'price' if snapshots == 1 else 'prices'} and no rosters. "
                "Deleting the season deletes every one of them -- the entire "
                "salary history for that year, which nothing can rebuild."
            )
        return (
            f"{obj.label} holds no rosters and no prices, so nothing goes with it. "
            "The season itself is gone for good, though."
        )

    def post(self, request, *args, **kwargs):
        obj = self.get_object()
        rosters, _ = self.counts(obj)
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
                f"{obj.label} has a roster in it now, so it cannot be deleted."
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


class ManagerNamePromptView(PromptView):
    """The half the create and rename prompts share: one nickname, checked.

    Both ask the same question of the same field, and both have to answer the
    same objection -- that the account already uses that handle. Split so the
    rule lives once; a rename excludes itself from the check, which is what
    lets someone save the dialog without renaming anything.
    """

    field_label = "Nickname"
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
            raise ValidationError(f'You already have a profile called "{value}".')
        return value

    def get_success_url(self, result):
        return reverse("fantasy:manager-list")


class ManagerCreateView(ManagerNamePromptView):
    title = "New manager profile"
    body = ""
    submit_label = "Create profile"

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

    title = "Rename profile"
    submit_label = "Save"

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

    title = "Delete this profile?"
    # No word to type. A profile that may be deleted holds nothing, so typing
    # DELETE would be friction guarding nothing -- and friction that guards
    # nothing teaches people to type it without reading. The word earns its
    # place on the roster dialog, where the loss is real.
    confirm_word = ""
    confirm_label = "Delete profile"

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
            context["cancel_label"] = "Close"
        return context

    def get_body(self, obj):
        held = self.held_rosters(obj)
        if held:
            names = ", ".join(f'"{roster.name}"' for roster in held)
            return (
                f'"{obj.nick_name}" still plays {names}. Delete or rename those '
                "first -- removing the profile would take them and their whole "
                "transaction history with it."
            )
        return (
            f'"{obj.nick_name}" plays no rosters, so nothing else goes with it. '
            "The handle is gone for good, though."
        )

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
        return self._roster


class RosterListView(LoginRequiredMixin, ListView):
    template_name = "fantasy/roster_list.html"
    context_object_name = "rosters"

    def get_queryset(self):
        queryset = Roster.objects.select_related("season", "manager").with_squad()
        if not (self.request.user.is_staff or self.request.user.is_superuser):
            queryset = queryset.filter(manager__user=self.request.user)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["current_season"] = Season.objects.filter(is_current=True).first()
        return context


class RosterCreateView(PromptView):
    """Asks for a name and a manager, then creates the roster.

    The manager is only asked for when there is something to decide -- see
    `get_choices`. The rules screen comes next either way.
    """

    title = "New roster"
    field_label = "Roster name"
    field_name = "name"
    placeholder = "e.g. Bulla Ballers"
    max_length = 80
    help_text = "You can rename it at any time."
    submit_label = "Create roster"
    choice_name = "manager"
    choice_label = "Manager"
    choice_help = "Which of your profiles plays this roster."
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
            raise ValidationError("Pick one of your manager profiles.") from None

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
                    f'{choice.nick_name} already has a roster called "{value}" this season.'
                )
            raise ValidationError(f'You already have a roster called "{value}" this season.')
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
            raise ValidationError("Pick one of the available roster icons.")
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
        return context


class RosterHistoryView(OwnRosterMixin, TemplateView):
    """Every recorded move, newest first, with the balance it left behind.

    The running balance is the point of the screen rather than decoration. It is
    computed forward from `STARTING_CASH` through the whole log, so the final
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

        balance = STARTING_CASH
        rows = []
        for move in moves:
            balance += move.cash_delta
            rows.append({"move": move, "balance": balance})

        context["roster"] = roster
        context["rows"] = list(reversed(rows))
        context["starting_cash"] = STARTING_CASH
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
    descending = sort in {"avg", "points", "hotness"} if direction is None else direction == "desc"
    key = ROSTER_SORT_FIELDS[sort]
    present = [membership for membership in memberships if key(membership.player) is not None]
    missing = [membership for membership in memberships if key(membership.player) is None]
    present.sort(key=lambda membership: key(membership.player), reverse=descending)
    return sort, ("desc" if descending else "asc"), present + missing


def _build_context(request, roster, error=None):
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
        from django.utils import timezone
        now = timezone.now()
        if now < roster.season.season_open_at:
            opened = timezone.localtime(roster.season.season_open_at)
            trade_unavailable_reason = (
                f"Trades become available when the season starts on {opened.strftime('%-d %B %Y')}."
            )
        else:
            trade_unavailable_reason = "The season has ended. Trading is closed."
    else:
        trade_unavailable_reason = ""

    can_buy_trade = trading_allowed and roster.cash >= Decimal("1500000")
    if not trading_allowed:
        buy_trade_disabled_reason = trade_unavailable_reason
    elif roster.cash < Decimal("1500000"):
        buy_trade_disabled_reason = "Not enough cash ($1.5M needed)"
    else:
        buy_trade_disabled_reason = ""

    can_sell_trade = trading_allowed and roster.trades_available > 0
    if not trading_allowed:
        sell_trade_disabled_reason = trade_unavailable_reason
    elif roster.trades_available <= 0:
        sell_trade_disabled_reason = "No trades available to sell"
    else:
        sell_trade_disabled_reason = ""

    return {
        "roster": roster,
        "filters": filters,
        "available": _available_players(roster, filters)[:100],
        "memberships": memberships,
        "current_sort": current_sort,
        "current_dir": current_dir,
        "positions": Player.Position.choices,
        "teams": Team.objects.order_by("name"),
        "roster_size": ROSTER_SIZE,
        "progress": roster.position_progress(),
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
        "signings_close_at": roster.season.signings_close_at,
        "error": error,
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
        raise NotImplementedError

    def post(self, request, *args, **kwargs):
        roster = self.get_roster()
        player = get_object_or_404(Player, pk=kwargs["player_pk"])

        error = None
        try:
            self.apply(roster, player)
        except ValidationError as exc:
            error = exc.messages[0]

        roster.refresh_from_db()
        return render(request, self.template_name, _build_context(request, roster, error))


class RosterBuyView(RosterChangeView):
    def apply(self, roster, player):
        if player.current_salary is None:
            raise ValidationError(f"No salary on record for {player}.")
        # The price is read here, never taken from the request: a client that
        # could name its own price could sign anyone for a euro.
        services.buy(roster, player, player.current_salary)


class RosterSellView(RosterChangeView):
    def apply(self, roster, player):
        services.sell(roster, player, services.amount_paid_for(roster, player))


class RosterBuyTradeView(OwnRosterMixin, ConfirmView):
    title = "Buy an available trade?"
    confirm_label = "Buy Trade ($1.5M)"
    tone = "primary"

    def get_object(self):
        return self.get_roster()

    def get_body(self, roster):
        cost_m = Decimal("1.5")
        current_m = roster.cash / Decimal("1000000")
        after_m = (roster.cash - Decimal("1500000")) / Decimal("1000000")
        return (
            f"Do you really want to buy 1 extra trade for ${cost_m:.2f}M cash? "
            f"Your cash balance will decrease from ${current_m:.2f}M to ${after_m:.2f}M."
        )

    def perform(self, roster):
        services.buy_trade(roster)
        roster.refresh_from_db()
        response = render(
            self.request,
            "fantasy/partials/build_update.html",
            _build_context(self.request, roster),
        )
        response["HX-Retarget"] = "#roster-panel"
        response["HX-Reswap"] = "outerHTML"
        return response


class RosterSellTradeView(OwnRosterMixin, ConfirmView):
    title = "Sell an available trade?"
    confirm_label = "Sell Trade (+$1.0M)"
    tone = "primary"

    def get_object(self):
        return self.get_roster()

    def get_body(self, roster):
        gain_m = Decimal("1.0")
        current_m = roster.cash / Decimal("1000000")
        after_m = (roster.cash + Decimal("1000000")) / Decimal("1000000")
        return (
            f"Do you really want to sell 1 trade for ${gain_m:.2f}M cash? "
            f"Your available trades will decrease from {roster.trades_available} to {roster.trades_available - 1}, "
            f"and your cash balance will increase from ${current_m:.2f}M to ${after_m:.2f}M."
        )

    def perform(self, roster):
        services.sell_trade(roster)
        roster.refresh_from_db()
        response = render(
            self.request,
            "fantasy/partials/build_update.html",
            _build_context(self.request, roster),
        )
        response["HX-Retarget"] = "#roster-panel"
        response["HX-Reswap"] = "outerHTML"
        return response


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

        return {
            "roster": roster,
            "memberships": memberships,
            "filters": filters,
            "positions": Player.Position.choices,
            "available": _available_players(roster, filters, budget=preview["budget"])[:60],
            "preview": preview,
            "trade_url": reverse("fantasy:roster-trade", args=[roster.pk]),
            # Ready-made query fragments, so a row that changes one side of the
            # trade does not drop the other.
            "keep_out": f"&out={player_out.pk}" if player_out else "",
            "keep_in": f"&in={player_in.pk}" if player_in else "",
            "state_query": "?body=1"
            + (f"&out={player_out.pk}" if player_out else "")
            + (f"&in={player_in.pk}" if player_in else ""),
            "panel_class": "modal-panel-xl",
            "error": error,
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
            error = "No trades available for this roster."
        elif not preview["ready"]:
            error = "Pick a player to send out and one to bring in."
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

        roster.refresh_from_db()
        response = render(
            request, "fantasy/partials/build_update.html", _build_context(request, roster)
        )
        response["HX-Trigger"] = "close-modal"
        return response

    def execute(self, roster, preview):
        player_out, player_in = preview["player_out"], preview["player_in"]
        for player in (player_out, player_in):
            if player.current_salary is None:
                raise ValidationError(f"No salary on record for {player}.")
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
    title = "Delete this roster?"
    confirm_label = "Delete permanently"

    def get_object(self):
        return self.get_roster()

    def get_body(self, obj):
        return (
            f'"{obj.name}" will be deleted along with its {obj.player_count} '
            "players and its entire transaction history. This cannot be undone."
        )

    def perform(self, obj):
        obj.delete()

    def get_success_url(self, obj):
        return reverse("fantasy:roster-list")
