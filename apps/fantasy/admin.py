from django.contrib import admin
from django.db.models import Count
from django.utils import timezone

from . import services
from .models import (
    Manager,
    PlanStep,
    PlayerSnapshot,
    Roster,
    RosterPlayer,
    Season,
    TradeGrant,
    TradePlan,
    Transaction,
    TransactionCorrection,
)


class TradeGrantInline(admin.TabularInline):
    model = TradeGrant
    extra = 0
    fields = ["label", "granted_at", "trades"]


@admin.register(Season)
class SeasonAdmin(admin.ModelAdmin):
    """Where the signings cutoff is set, which is the only screen that sets it.

    Deliberately not exposed in the app: closing signings is an administrator's
    decision about the game, not a player's about their roster.
    """

    list_display = ["label", "starts_on", "ends_on", "is_current", "signings"]
    list_filter = ["is_current"]
    inlines = [TradeGrantInline]

    # A season's snapshots go with it, past post_save: recount everyone, as
    # SeasonDeleteView does.
    def delete_model(self, request, obj):
        super().delete_model(request, obj)
        services.refresh_all_hotness()

    def delete_queryset(self, request, queryset):
        super().delete_queryset(request, queryset)
        services.refresh_all_hotness()

    @admin.display(description="Signings", ordering="signings_close_at")
    def signings(self, obj):
        """Open, or the moment it closed -- not just the stored value.

        A timestamp alone does not say whether it has passed, and that is the
        only thing anyone reads this column for.
        """
        if obj.signings_open_at and not obj.signings_open:
            opened = timezone.localtime(obj.signings_open_at)
            return f"Opens {opened.strftime('%-d %b %Y, %H:%M')}"
        if obj.signings_close_at is None:
            return "Open all season"
        closed = timezone.localtime(obj.signings_close_at)
        when = closed.strftime("%-d %b %Y, %H:%M")
        return f"Closed {when}" if not obj.signings_open else f"Closes {when}"


class RosterPlayerInline(admin.TabularInline):
    model = RosterPlayer
    extra = 0
    autocomplete_fields = ["player"]
    readonly_fields = ["added_at", "removed_at"]
    can_delete = False


@admin.register(Manager)
class ManagerAdmin(admin.ModelAdmin):
    list_display = ["nick_name", "user", "roster_count"]
    search_fields = ["nick_name", "user__email"]
    list_select_related = ["user"]
    autocomplete_fields = ["user"]
    readonly_fields = ["created_at", "updated_at"]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_rosters=Count("rosters"))

    @admin.display(description="Rosters", ordering="_rosters")
    def roster_count(self, obj):
        return obj._rosters


@admin.register(Roster)
class RosterAdmin(admin.ModelAdmin):
    list_display = ["name", "manager", "season", "cash", "player_count", "is_complete", "is_active"]
    list_filter = ["season", "is_active"]
    # The account as well as the handle: the handle is what the game shows, but
    # the email is what you have when someone writes in about their roster.
    search_fields = ["name", "manager__nick_name", "manager__user__email"]
    list_select_related = ["manager", "manager__user", "season"]
    autocomplete_fields = ["manager"]
    inlines = [RosterPlayerInline]
    # The balance and the trade count are what the ledger adds up to, so they
    # are not typed in here: an edit would leave the history page saying the
    # two disagree, with nothing to show how. A wrong move is corrected by the
    # manager on that page (`services.move_transaction`, `undo_transaction`),
    # which keeps all three records in step. The starting budget is the
    # ledger's first line, so it is locked for the same reason.
    readonly_fields = ["starting_cash", "cash", "trades_available"]

    def get_queryset(self, request):
        # Both derived columns below are counted from the squad, so the
        # changelist loads every squad at once instead of twice per row.
        return super().get_queryset(request).with_squad()

    @admin.display(description="Players")
    def player_count(self, obj):
        return obj.player_count

    @admin.display(boolean=True, description="Complete")
    def is_complete(self, obj):
        return obj.is_complete


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    """A ledger, so read-only -- as `PlayerSnapshotAdmin` is, for the same reason.

    `Roster.cash` is a balance that only these rows move, which means editing or
    deleting one here breaks the agreement between the two silently and with
    nothing to detect it. A mistaken move is corrected by recording the move that
    reverses it, which is also the version of events that actually happened.
    """

    list_display = [
        "occurred_at",
        "roster",
        "kind_display",
        "description",
        "price_out",
        "price_in",
        "cash_delta",
    ]
    list_filter = ["roster__season"]
    list_select_related = ["roster", "player_in", "player_out"]
    date_hierarchy = "occurred_at"
    readonly_fields = [f.name for f in Transaction._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Kind")
    def kind_display(self, obj):
        return obj.get_kind_display()


@admin.register(TransactionCorrection)
class TransactionCorrectionAdmin(admin.ModelAdmin):
    """What managers corrected in their own history: read-only, like the ledger."""

    list_display = [
        "created_at",
        "roster",
        "action",
        "kind",
        "player_out",
        "player_in",
        "occurred_at_before",
        "occurred_at_after",
        "cash_delta_before",
        "cash_delta_after",
        "made_by",
    ]
    list_filter = ["action", "kind", "roster__season"]
    search_fields = ["roster__name", "roster__manager__nick_name", "made_by__email"]
    list_select_related = ["roster", "player_out", "player_in", "made_by"]
    date_hierarchy = "created_at"
    readonly_fields = [f.name for f in TransactionCorrection._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PlayerSnapshot)
class PlayerSnapshotAdmin(admin.ModelAdmin):
    list_display = ["player", "as_of", "salary", "total_fp", "games_played", "fp_per_game", "team"]
    list_filter = ["season", "position"]
    search_fields = ["player__first_name", "player__last_name"]
    list_select_related = ["player", "team"]
    date_hierarchy = "as_of"
    # Snapshots are a record of what the source said; editing one would be
    # rewriting history rather than correcting it.
    readonly_fields = [f.name for f in PlayerSnapshot._meta.fields]

    def has_add_permission(self, request):
        return False

    # Deleting a wrong row is still allowed; the players it belonged to get
    # their stored hotness recounted from what is left.
    def delete_model(self, request, obj):
        super().delete_model(request, obj)
        services.refresh_hotness(obj.player_id)

    def delete_queryset(self, request, queryset):
        player_ids = set(queryset.values_list("player_id", flat=True))
        super().delete_queryset(request, queryset)
        for player_id in player_ids:
            services.refresh_hotness(player_id)


class PlanStepInline(admin.TabularInline):
    model = PlanStep
    extra = 0
    fields = ["processing_date", "order", "kind", "player_out", "player_in"]
    autocomplete_fields = ["player_out", "player_in"]


@admin.register(TradePlan)
class TradePlanAdmin(admin.ModelAdmin):
    """Plans are the manager's own scratch work; here only to look into one."""

    list_display = ["name", "roster", "step_count", "is_shared", "updated_at"]
    list_filter = ["roster__season"]
    search_fields = ["name", "roster__name", "roster__manager__nick_name"]
    list_select_related = ["roster"]
    readonly_fields = ["created_at", "updated_at"]
    inlines = [PlanStepInline]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_steps=Count("steps"))

    @admin.display(description="Steps", ordering="_steps")
    def step_count(self, obj):
        return obj._steps

    @admin.display(boolean=True, description="Shared")
    def is_shared(self, obj):
        return obj.is_shared
