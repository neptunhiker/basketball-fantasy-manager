"""The write layer for the fantasy app.

Two groups of functions, both here for the same reason: they each keep records
in step that would otherwise drift apart.

`buy`/`sell`/`trade` -- membership, cash and the transaction log are three
records of the same event, so every change is atomic and goes through here.

`record_snapshot` -- a scraped snapshot is also the newest word on which team a
player is on, so writing one may update `Player` too.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import (
    ROSTER_SIZE,
    STARTING_TRADES,
    TRADE_BUY_PRICE,
    TRADE_SELL_PRICE,
    Manager,
    PlayerSnapshot,
    Roster,
    RosterPlayer,
    ROSTER_ICON_CHOICES,
    Transaction,
    WatchlistEntry,
    position_shortfalls,
)


def _lock(roster):
    """Re-read the roster inside the transaction so two concurrent buys cannot
    both see the same balance and both decide it is affordable."""
    return Roster.objects.select_for_update().get(pk=roster.pk)


def _open_membership(roster, player):
    return RosterPlayer.objects.open().filter(roster=roster, player=player).first()


def _require_transactions(season):
    """Refuse a buy or a sell outside the season's transaction window.

    Checked here rather than in the views so the rule holds for every writer --
    the screens, the shell, a management command, a future importer. There is
    no way to reach `Roster.cash` that does not come through this module.

    Trades are deliberately not covered. They are the move that stays available
    all season: after the cutoff a roster is frozen in size, not in shape.
    """
    if season.transactions_allowed:
        return
    if season.signings_open_at and timezone.now() < season.signings_open_at:
        opened = timezone.localtime(season.signings_open_at)
        raise ValidationError(
            f"Signings open on {opened.strftime('%-d %B at %H:%M')}."
        )
    if season.signings_close_at is None:
        raise ValidationError("Buying and releasing are not currently allowed.")
    closed = timezone.localtime(season.signings_close_at)
    raise ValidationError(
        f"Signings closed on {closed.strftime('%-d %B at %H:%M')}. Trades only from here."
    )


@transaction.atomic
def buy(roster, player, price, occurred_at=None, note=""):
    """Add a player to the roster and pay for them."""
    occurred_at = occurred_at or timezone.now()
    price = Decimal(price)
    locked = _lock(roster)

    # Inside the transaction and after the lock, so a cutoff that passes
    # mid-request cannot let one last purchase through behind it.
    _require_transactions(locked.season)

    if price < 0:
        raise ValidationError("A purchase price cannot be negative.")
    if price > locked.cash:
        raise ValidationError(
            f"Not enough cash: {locked.cash:,.0f} available, {price:,.0f} needed."
        )
    if _open_membership(locked, player):
        raise ValidationError(f"{player} is already on the roster.")
    if locked.player_count >= ROSTER_SIZE:
        raise ValidationError(f"The roster is full at {ROSTER_SIZE} players.")

    RosterPlayer.objects.create(roster=locked, player=player, added_at=occurred_at)
    locked.cash -= price
    locked.save(update_fields=["cash", "updated_at"])
    roster.refresh_from_db()

    return Transaction.objects.create(
        roster=locked,
        occurred_at=occurred_at,
        player_in=player,
        price_in=price,
        cash_delta=-price,
        note=note,
    )


@transaction.atomic
def sell(roster, player, price, occurred_at=None, note=""):
    """Remove a player from the roster and take the money."""
    occurred_at = occurred_at or timezone.now()
    price = Decimal(price)
    locked = _lock(roster)

    _require_transactions(locked.season)

    if price < 0:
        raise ValidationError("A sale price cannot be negative.")
    membership = _open_membership(locked, player)
    if membership is None:
        raise ValidationError(f"{player} is not on the roster.")

    # The spell is closed rather than deleted: the roster's history should still
    # show that this player was once part of it.
    membership.removed_at = occurred_at
    membership.save(update_fields=["removed_at", "updated_at"])
    locked.cash += price
    locked.save(update_fields=["cash", "updated_at"])
    roster.refresh_from_db()

    return Transaction.objects.create(
        roster=locked,
        occurred_at=occurred_at,
        player_out=player,
        price_out=price,
        cash_delta=price,
        note=note,
    )


@transaction.atomic
def trade(roster, player_out, player_in, price_out, price_in, occurred_at=None, note=""):
    """Swap one player for another in a single move.

    Recorded as one transaction rather than a sell followed by a buy, because
    that is what it is -- and because the intermediate state, where the roster
    is one player short, never really existed.
    """
    occurred_at = occurred_at or timezone.now()
    price_out, price_in = Decimal(price_out), Decimal(price_in)
    locked = _lock(roster)

    if not locked.season.trading_allowed:
        raise ValidationError("Trades are only allowed while the season is running.")

    if locked.trades_available <= 0:
        raise ValidationError("No trades available for this roster.")

    if player_out == player_in:
        raise ValidationError("The player in and the player out cannot be the same.")
    membership = _open_membership(locked, player_out)
    if membership is None:
        raise ValidationError(f"{player_out} is not on the roster.")
    if _open_membership(locked, player_in):
        raise ValidationError(f"{player_in} is already on the roster.")

    delta = price_out - price_in
    if locked.cash + delta < 0:
        raise ValidationError(
            f"Not enough cash: {locked.cash + price_out:,.0f} available, {price_in:,.0f} needed."
        )

    membership.removed_at = occurred_at
    membership.save(update_fields=["removed_at", "updated_at"])
    RosterPlayer.objects.create(roster=locked, player=player_in, added_at=occurred_at)
    locked.cash += delta
    locked.trades_available -= 1
    locked.save(update_fields=["cash", "trades_available", "updated_at"])
    roster.refresh_from_db()

    return Transaction.objects.create(
        roster=locked,
        occurred_at=occurred_at,
        player_in=player_in,
        player_out=player_out,
        # Both sides, not just the net: a trade is the one move whose two
        # valuations are otherwise lost the moment it is written.
        price_in=price_in,
        price_out=price_out,
        cash_delta=delta,
        kind=Transaction.Kind.TRADE,
        note=note,
    )


@transaction.atomic
def buy_trade(roster, occurred_at=None, note=""):
    """Buy an extra trade for $1.5M."""
    occurred_at = occurred_at or timezone.now()
    locked = _lock(roster)

    if not locked.season.trading_allowed:
        raise ValidationError("Trades are only allowed while the season is live.")

    if locked.cash < TRADE_BUY_PRICE:
        raise ValidationError(
            f"Not enough cash: {locked.cash:,.0f} available, {TRADE_BUY_PRICE:,.0f} needed to buy a trade."
        )

    locked.cash -= TRADE_BUY_PRICE
    locked.trades_available += 1
    locked.save(update_fields=["cash", "trades_available", "updated_at"])
    roster.refresh_from_db()

    return Transaction.objects.create(
        roster=locked,
        occurred_at=occurred_at,
        cash_delta=-TRADE_BUY_PRICE,
        kind=Transaction.Kind.BUY_TRADE,
        note=note or "Bought 1 trade",
    )


@transaction.atomic
def sell_trade(roster, occurred_at=None, note=""):
    """Sell an available trade for $1.0M cash."""
    occurred_at = occurred_at or timezone.now()
    locked = _lock(roster)

    if not locked.season.trading_allowed:
        raise ValidationError("Trades are only allowed while the season is live.")

    if locked.trades_available <= 0:
        raise ValidationError("No trades available to sell.")

    locked.cash += TRADE_SELL_PRICE
    locked.trades_available -= 1
    locked.save(update_fields=["cash", "trades_available", "updated_at"])
    roster.refresh_from_db()

    return Transaction.objects.create(
        roster=locked,
        occurred_at=occurred_at,
        cash_delta=TRADE_SELL_PRICE,
        kind=Transaction.Kind.SELL_TRADE,
        note=note or "Sold 1 trade",
    )


# --- snapshots ---------------------------------------------------------------


def sync_player_from_latest_snapshot(player):
    """Point `Player.team` and `Player.position` at the newest snapshot.

    Deliberately reads the newest row rather than trusting whichever snapshot
    was just written. Backfilling an archived page would otherwise drag a traded
    player's current team back to where they used to be -- and that looks like
    data, not a bug.

    Returns the fields it changed, so an import can report how much moved.
    """
    latest = player.snapshots.order_by("-as_of").first()
    if latest is None:
        return []

    # (reported name, attribute to write, value from the snapshot). The team is
    # compared by id so that reading the newest snapshot does not also fetch the
    # Team row just to throw it away.
    mirrored = [
        ("team", "team_id", latest.team_id),
        ("position", "position", latest.position),
        ("current_salary", "current_salary", latest.salary),
        ("current_total_fp", "current_total_fp", latest.total_fp),
        # A generated column, so this is what Postgres computed -- NULL before
        # the first game rather than a division by zero.
        ("current_fp_per_game", "current_fp_per_game", latest.fp_per_game),
        ("current_games_played", "current_games_played", latest.games_played),
    ]

    changed = []
    for name, attribute, value in mirrored:
        if getattr(player, attribute) != value:
            setattr(player, attribute, value)
            changed.append(name)

    if changed:
        player.save(update_fields=[*changed, "updated_at"])
    return changed


@transaction.atomic
def record_snapshot(
    player, season, as_of, salary, total_fp, games_played, team=None, position=None
):
    """Store one scraped snapshot and bring `Player` up to date.

    Idempotent: re-running an import for a moment already recorded updates that
    row instead of adding a second one.
    """
    snapshot, created = PlayerSnapshot.objects.update_or_create(
        player=player,
        season=season,
        as_of=as_of,
        defaults={
            "salary": Decimal(salary),
            "total_fp": Decimal(total_fp),
            "games_played": games_played,
            "team": team,
            "position": position or player.position,
        },
    )
    sync_player_from_latest_snapshot(player)
    return snapshot, created


# --- helpers the roster screens need -----------------------------------------


def default_nickname(user):
    """A first handle for a new manager, from whatever the account knows.

    Deliberately a guess and not a decision: the point is that nobody has to
    invent a name before they can build a roster. It is one edit to change
    afterwards, and `Manager.nick_name` is not unique across users, so two
    people whose accounts suggest the same handle is not a conflict.
    """
    return (user.first_name or user.email.split("@")[0])[:40]


def manager_for(user):
    """The profile a new roster would belong to, or None if there is not one.

    A read, deliberately: the roster form asks this question on a GET, and a GET
    that creates a profile would leave one behind for everybody who opened the
    dialog and thought better of it. `ensure_manager` is the writing half.

    Returns the oldest profile when there are several. That is a placeholder,
    not a rule: age is standing in for a choice nobody has been asked to make.
    The day the app has a notion of viewing *as* one profile, the choice belongs
    in the request rather than here.
    """
    return user.manager_profiles.order_by("created_at").first()


def ensure_manager(user):
    """The profile a new roster belongs to, creating it if this is the first.

    A user becomes a manager by building something to manage, so the profile
    appears with the first roster rather than with the account -- which keeps
    the model's promise that a manager is someone who manages something.
    """
    existing = manager_for(user)
    if existing is not None:
        return existing
    # `get_or_create` rather than `create`, because a double-submitted form is
    # two concurrent first rosters and would otherwise be an IntegrityError.
    # `unique_nickname_per_user` is what makes the retry land on the same row.
    manager, _ = Manager.objects.get_or_create(user=user, nick_name=default_nickname(user))
    return manager


def default_roster_name(manager, season):
    """The name the creation prompt suggests: `Roster 1`, `Roster 2`, ...

    The lowest free number rather than one past the count, because rosters are
    named by hand now -- someone with a single "Bulla Ballers" should still be
    offered "Roster 1".

    Asked of a manager, not of an account, so the suggestion matches what the
    uniqueness constraint actually enforces. Two of one user's profiles are each
    offered "Roster 1", because each of them can have one.

    `manager` may be None, which is the state of anyone about to build their
    first roster: no profile means no rosters, so every name is free.
    """
    if manager is None:
        return "Roster 1"

    taken = set(manager.rosters.filter(season=season).values_list("name", flat=True))
    index = 1
    while f"Roster {index}" in taken:
        index += 1
    return f"Roster {index}"


def create_roster(manager, season, name, icon="koala"):
    """Start a roster, if the season will still let one be filled.

    Here rather than in the view purely so the cutoff has one home. A roster
    begun after signings close would open at 0 of 15 and stay there for good --
    trades swap players, they cannot add a sixteenth -- so it is refused
    outright rather than created as something that can never be finished.

    Unlike `buy` and `sell` this is not a balance change, so there is no lock
    and no transaction of its own. The rule is the only reason the function
    exists.
    """
    if icon not in dict(ROSTER_ICON_CHOICES):
        raise ValidationError("Pick one of the available roster icons.")
    if not season.transactions_allowed:
        if season.signings_open_at and timezone.now() < season.signings_open_at:
            opened = timezone.localtime(season.signings_open_at)
            raise ValidationError(f"Signings open on {opened.strftime('%-d %B at %H:%M')}.")
        raise ValidationError("A new roster cannot be created outside the signing window.")
    return Roster.objects.create(manager=manager, season=season, name=name, icon=icon)


def trade_preview(roster, player_out, player_in):
    """What a trade would do to a roster, for the screen that offers it.

    Both sides are priced at today's salary, which is the decision actually
    being made: the roster gives a player up at his market price and takes one
    on at his. Deliberately not `amount_paid_for`, which is right for a sale --
    a sale is a refund of a purchase -- but wrong here. A player who has gained
    value since he was bought is worth that gain in a trade, and realising that
    gain is the point of trading at all.

    Either side may still be None while the user has only made half the choice,
    so every figure has to survive a half-made decision. A player the source has
    never reported a salary for counts as zero here; refusing the trade outright
    is the confirm step's job, not the preview's.
    """
    salary_out = _current_salary(player_out)
    salary_in = _current_salary(player_in)
    delta = salary_out - salary_in
    cash_after = roster.cash + delta

    counts = roster.position_counts()
    before = position_shortfalls(counts)
    after = dict(counts)
    if player_out is not None:
        after[player_out.position] -= 1
    if player_in is not None:
        after[player_in.position] += 1
    # Only what this trade would newly break. A roster already short a Center
    # does not need telling again by a trade that never touched its Centers.
    breaks = [gap for gap in position_shortfalls(after) if gap not in before]

    return {
        "player_out": player_out,
        "player_in": player_in,
        "salary_out": salary_out,
        "salary_in": salary_in,
        "delta": delta,
        "cash_after": cash_after,
        "affordable": cash_after >= 0,
        "trades_available": roster.trades_available,
        "has_trades": roster.trades_available > 0,
        # What the incoming side is chosen against: the balance plus whatever
        # the outgoing player frees up.
        "budget": roster.cash + salary_out,
        "breaks": breaks,
        "ready": player_out is not None and player_in is not None,
    }


def _current_salary(player):
    if player is None or player.current_salary is None:
        return Decimal("0")
    return player.current_salary


def amount_paid_for(roster, player):
    """What this roster paid for a player, so removing them refunds exactly.

    Reads the last move that brought the player in, whether that was a purchase
    or a trade: both record `price_in`, so a player acquired in a swap now has a
    real cost basis rather than a guess. The current salary remains the fallback
    for a row written before prices were kept per side, and for a player whose
    arrival was never recorded at all.
    """
    arrival = (
        Transaction.objects.filter(roster=roster, player_in=player).order_by("-occurred_at").first()
    )
    if arrival is not None and arrival.price_in is not None:
        return arrival.price_in
    return player.current_salary or Decimal("0")


def add_to_watchlist(user, player):
    """Add one player to this account's private watchlist idempotently."""
    return WatchlistEntry.objects.get_or_create(user=user, player=player)


def remove_from_watchlist(user, player):
    """Remove only this account's entry for the player."""
    return WatchlistEntry.objects.filter(user=user, player=player).delete()
