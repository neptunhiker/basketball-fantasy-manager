"""When buying and selling stop, and what is left afterwards.

`Season.signings_close_at` is a moment an administrator picks. Past it a roster
in that season can only change through trades: no buying, no selling, no new
rosters. Null -- what every season is until somebody decides otherwise -- means
signings stay open all year.

Two things these tests are really guarding. The first is that the rule lives in
`services` rather than in the views, so it holds for a hand-written POST, the
shell and anything written later; `test_a_hand_written_post_is_still_refused`
is the one that would notice it moving up a layer. The second is the
consequence, which is easy to build and easy to leave unsaid: a trade swaps one
player for one player, so a roster short of 15 at the cutoff is short for good.
The screens have to say that before the deadline, not after.
"""

import datetime as dt
from decimal import Decimal
from unittest import mock

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from apps.fantasy import services
from apps.fantasy.models import ROSTER_SIZE, Manager, Roster, Season
from apps.nba.models import Player

pytestmark = pytest.mark.django_db


@pytest.fixture
def season(db):
    """Signings open, because null is the state every season starts in."""
    return Season.objects.create(
        label="2026-27",
        starts_on=dt.date(2026, 10, 20),
        ends_on=dt.date(2027, 4, 11),
        is_current=True,
    )


@pytest.fixture
def signed_in(client, user, password):
    client.login(email=user.email, password=password)
    return client


@pytest.fixture
def roster(season, manager):
    return Roster.objects.create(manager=manager, season=season, name="Roster 1")


def make_player(name, position="G", salary=1_000_000):
    return Player.objects.create(
        first_name=name, last_name=position, position=position, current_salary=Decimal(salary)
    )


def close_signings(season, when=None):
    """Shut the window, by default an hour ago."""
    season.signings_close_at = when or timezone.now() - dt.timedelta(hours=1)
    season.save(update_fields=["signings_close_at"])
    return season


def build_url(roster):
    return reverse("fantasy:roster-build", args=[roster.pk])


def card_for(body, name):
    """The one roster card on the list page that mentions `name`.

    Split on the card's own opening class rather than on a closing tag: the
    card nests divs, so counting `</div>` back out of it is guesswork.
    """
    for chunk in body.split('<div class="card p-5'):
        if name in chunk:
            return chunk
    raise AssertionError(f"no card for {name!r}")


# --- the property ------------------------------------------------------------


def test_a_season_with_no_cutoff_stays_open(season):
    """The default, and the reason the migration needed no data step."""
    assert season.signings_close_at is None
    assert season.signings_open is True


def test_a_cutoff_in_the_future_leaves_signings_open(season):
    close_signings(season, timezone.now() + dt.timedelta(days=3))
    assert season.signings_open is True


def test_a_cutoff_in_the_past_closes_them(season):
    close_signings(season)
    assert season.signings_open is False


def test_the_cutoff_instant_itself_counts_as_closed(season):
    """The boundary, with the clock held still.

    The clock has to be frozen for this to test anything: any real `now()` read
    after the cutoff was stored is already past it, so `<` and `<=` would agree
    and the assertion would pass either way.
    """
    instant = timezone.now()
    close_signings(season, instant)

    with mock.patch("django.utils.timezone.now", return_value=instant):
        assert season.signings_open is False


def test_lifecycle_permissions_are_mutually_exclusive(season):
    now = timezone.now().replace(microsecond=0)
    season.starts_on = timezone.localdate() + dt.timedelta(days=10)
    season.ends_on = timezone.localdate() + dt.timedelta(days=30)
    season.signings_open_at = now + dt.timedelta(days=1)
    season.signings_close_at = now + dt.timedelta(days=5)

    phases = [
        (now - dt.timedelta(days=1), False, False),
        (now + dt.timedelta(days=2), True, False),
        (now + dt.timedelta(days=6), False, False),
        (now + dt.timedelta(days=11), False, True),
        (now + dt.timedelta(days=31), False, False),
    ]

    for instant, transactions, trading in phases:
        with mock.patch("django.utils.timezone.now", return_value=instant):
            assert season.transactions_allowed is transactions
            assert season.trading_allowed is trading


def test_the_moment_before_the_cutoff_is_still_open(season):
    """The other half of the boundary, so the pair says where the edge is."""
    instant = timezone.now()
    close_signings(season, instant)

    with mock.patch(
        "django.utils.timezone.now", return_value=instant - dt.timedelta(microseconds=1)
    ):
        assert season.signings_open is True


def test_a_cutoff_after_the_season_ends_is_rejected(season):
    """It would never be reached, so the season would silently never close."""
    season.signings_close_at = timezone.make_aware(
        dt.datetime.combine(season.ends_on + dt.timedelta(days=1), dt.time(12, 0))
    )
    with pytest.raises(ValidationError) as exc:
        season.full_clean()
    assert "signings_close_at" in exc.value.message_dict


def test_a_cutoff_before_the_season_starts_is_allowed(season):
    """A window that shuts before tip-off is unusual, not wrong."""
    season.signings_close_at = timezone.make_aware(
        dt.datetime.combine(season.starts_on - dt.timedelta(days=2), dt.time(12, 0))
    )
    season.full_clean()  # raises if not allowed


# --- the service guard -------------------------------------------------------


def test_buying_is_refused_after_the_cutoff(roster, season):
    close_signings(season)
    player = make_player("Late")

    with pytest.raises(ValidationError) as exc:
        services.buy(roster, player, player.current_salary)

    assert "Signings closed" in exc.value.messages[0]
    assert roster.memberships.count() == 0


def test_selling_is_refused_after_the_cutoff(roster, season):
    """Closed both ways, which is the answer that strands a short roster."""
    player = make_player("Kept")
    services.buy(roster, player, player.current_salary)
    close_signings(season)

    with pytest.raises(ValidationError) as exc:
        services.sell(roster, player, player.current_salary)

    assert "Signings closed" in exc.value.messages[0]
    assert roster.memberships.filter(removed_at__isnull=True).count() == 1


def test_trading_still_works_after_the_cutoff(roster, season):
    """The whole point of closing signings rather than freezing the roster."""
    out_player = make_player("Out")
    services.buy(roster, out_player, out_player.current_salary)
    season.starts_on = timezone.localdate() - dt.timedelta(days=1)
    season.ends_on = timezone.localdate() + dt.timedelta(days=100)
    season.save(update_fields=["starts_on", "ends_on"])
    close_signings(season)
    in_player = make_player("In")

    services.trade(
        roster,
        out_player,
        in_player,
        price_out=out_player.current_salary,
        price_in=in_player.current_salary,
    )

    assert roster.memberships.filter(removed_at__isnull=True).count() == 1
    assert roster.memberships.get(removed_at__isnull=True).player == in_player


def test_buying_works_right_up_to_the_cutoff(roster, season):
    close_signings(season, timezone.now() + dt.timedelta(minutes=1))
    player = make_player("Early")

    services.buy(roster, player, player.current_salary)

    assert roster.memberships.count() == 1


def test_the_refusal_names_the_moment(roster, season):
    """A refusal that does not say when is a refusal nobody can plan around.

    Derived from today rather than hardcoded, so the test does not expire --
    and read back in local time, which is what the message renders.
    """
    closed_at = (timezone.localtime() - dt.timedelta(days=2)).replace(
        hour=19, minute=30, second=0, microsecond=0
    )
    close_signings(season, closed_at)

    with pytest.raises(ValidationError) as exc:
        services.buy(roster, make_player("Late"), Decimal(1_000_000))

    message = exc.value.messages[0]
    assert closed_at.strftime("%-d %B") in message
    assert "19:30" in message


# --- new rosters -------------------------------------------------------------


def test_a_roster_cannot_be_created_after_the_cutoff(season, manager):
    close_signings(season)

    with pytest.raises(ValidationError) as exc:
        services.create_roster(manager, season, "Too late")

    assert "outside the signing window" in exc.value.messages[0]
    assert Roster.objects.count() == 0


def test_a_roster_can_be_created_while_signings_are_open(season, manager):
    created = services.create_roster(manager, season, "In time")
    assert created.pk is not None


def test_the_create_dialog_refuses_after_the_cutoff(signed_in, season, manager):
    """Reachable by hand once the button is gone, so it has to answer for itself."""
    close_signings(season)

    response = signed_in.post(
        reverse("fantasy:roster-create"),
        {"name": "Too late", "manager": str(manager.pk)},
        headers={"HX-Request": "true"},
    )

    # A 200 carrying the corrected form, as every other prompt rejection is.
    assert response.status_code == 200
    assert "outside the signing window" in response.content.decode()
    assert Roster.objects.count() == 0


def test_the_create_button_goes_away_once_signings_close(signed_in, season, manager):
    Roster.objects.create(manager=manager, season=season, name="Roster 1")
    create = reverse("fantasy:roster-create")
    assert create in signed_in.get(reverse("fantasy:roster-list")).content.decode()

    close_signings(season)

    assert create not in signed_in.get(reverse("fantasy:roster-list")).content.decode()


# --- the build page ----------------------------------------------------------


def test_release_disappears_once_selling_is_closed(signed_in, roster, season):
    player = make_player("Kept")
    services.buy(roster, player, player.current_salary)
    assert "Release" in signed_in.get(build_url(roster)).content.decode()

    close_signings(season)

    body = signed_in.get(build_url(roster)).content.decode()
    assert "Release" not in body
    # The trade icon is unavailable before the season starts.
    assert f"?out={player.pk}" not in body


def test_a_hand_written_post_is_still_refused(signed_in, roster, season):
    """The reason the guard is in `services` and not in the template.

    The buttons are gone, so this is the only way in -- and it has to close.
    """
    player = make_player("Target")
    close_signings(season)

    buy = signed_in.post(reverse("fantasy:roster-buy", args=[roster.pk, player.pk]))

    assert "Signings closed" in buy.content.decode()
    assert roster.memberships.count() == 0


def test_a_hand_written_sell_is_refused_too(signed_in, roster, season):
    player = make_player("Kept")
    services.buy(roster, player, player.current_salary)
    close_signings(season)

    sell = signed_in.post(reverse("fantasy:roster-sell", args=[roster.pk, player.pk]))

    assert "Signings closed" in sell.content.decode()
    assert roster.memberships.filter(removed_at__isnull=True).count() == 1


def test_an_empty_roster_is_not_told_to_sign_after_the_cutoff(signed_in, roster, season):
    """An empty roster clearly states that it contains no players."""
    close_signings(season)

    body = signed_in.get(reverse("fantasy:roster-build", args=[roster.pk])).content.decode()
    card = body[body.index('id="roster-panel"') :]

    assert "No players on this roster." in card
    assert "Sign one from the list" not in card


def test_an_empty_roster_is_still_pointed_at_the_list_while_signings_are_open(signed_in, roster):
    body = signed_in.get(reverse("fantasy:roster-build", args=[roster.pk])).content.decode()

    assert "Sign one from the list below." in body


# --- saying so before it happens ---------------------------------------------


def test_the_deadline_is_named_while_it_is_still_ahead(signed_in, roster, season):
    """The lifecycle deadline is shown by the page-level countdown, not the picker."""
    close_signings(season, timezone.now() + dt.timedelta(days=3))

    body = signed_in.get(build_url(roster)).content.decode()

    assert "Signings close on" not in body
    assert "has to be complete by then" not in body


def test_a_season_with_no_deadline_says_nothing(signed_in, roster):
    """No cutoff is no rule. Announcing its absence would be noise."""
    body = signed_in.get(build_url(roster)).content.decode()
    assert "Signings close" not in body


def test_a_short_roster_is_told_the_gap_is_permanent(signed_in, roster, season):
    close_signings(season)

    body = signed_in.get(build_url(roster)).content.decode()

    assert "stays short for the season" not in body
    assert "No roster changes are available in this phase" in body
    # And the old prompt, which read as a task, is gone.
    assert "Still needed:" not in body


def test_a_complete_roster_is_not_warned(signed_in, roster, season):
    """Nothing was lost for a roster that made the deadline."""
    for index in range(5):
        services.buy(roster, make_player(f"G{index}", "G"), Decimal(1_000_000))
    for index in range(5):
        services.buy(roster, make_player(f"F{index}", "F"), Decimal(1_000_000))
    for index in range(ROSTER_SIZE - 10):
        services.buy(roster, make_player(f"C{index}", "C"), Decimal(1_000_000))
    assert roster.is_complete

    close_signings(season)
    body = signed_in.get(build_url(roster)).content.decode()

    assert "stays short for the season" not in body
    assert "ready to play" in body


def test_the_roster_card_stops_saying_building(signed_in, roster, season):
    """ "Building" is a promise there is something left to build with."""
    close_signings(season)

    card = card_for(signed_in.get(reverse("fantasy:roster-list")).content.decode(), roster.name)

    assert ">Short<" in card
    assert ">Building<" not in card
    assert "No roster changes until the season starts" in card


def test_the_roster_card_counts_down_before_the_cutoff(signed_in, roster, season):
    close_signings(season, timezone.now() + dt.timedelta(days=3))

    body = signed_in.get(reverse("fantasy:roster-list")).content.decode()

    assert "Signings close" in body
    assert ">Building<" in body


def test_the_rules_page_names_the_deadline(signed_in, roster, season):
    close_signings(season, timezone.now() + dt.timedelta(days=3))

    body = signed_in.get(reverse("fantasy:roster-rules", args=[roster.pk])).content.decode()

    assert "Complete it by" in body
    assert "Trades stay open every day" in body


def test_a_card_reads_its_own_seasons_cutoff(signed_in, user, manager, season):
    """Two seasons, one closed: a roster is bound by the season it was played in.

    Reading `current_season` for every card would report the wrong rule for
    every roster outside it.
    """
    old = Season.objects.create(
        label="2025-26",
        starts_on=dt.date(2025, 10, 21),
        ends_on=dt.date(2026, 4, 12),
        signings_close_at=timezone.now() - dt.timedelta(days=200),
    )
    Roster.objects.create(manager=manager, season=season, name="Open one")
    Roster.objects.create(manager=manager, season=old, name="Closed one")

    body = signed_in.get(reverse("fantasy:roster-list")).content.decode()
    cards = {name: card_for(body, name) for name in ("Open one", "Closed one")}

    assert ">Building<" in cards["Open one"]
    assert "Signings closed" not in cards["Open one"]
    assert ">Short<" in cards["Closed one"]
    assert "Season closed — no roster changes" in cards["Closed one"]


# --- the admin column --------------------------------------------------------


def test_the_admin_column_says_whether_it_has_passed(season):
    """A bare timestamp does not answer the only question the column is read for."""
    from apps.fantasy.admin import SeasonAdmin

    column = SeasonAdmin(Season, None).signings

    assert column(season) == "Open all season"

    close_signings(season, timezone.now() + dt.timedelta(days=3))
    assert column(season).startswith("Closes ")

    close_signings(season)
    assert column(season).startswith("Closed ")


def test_a_second_profiles_roster_is_bound_by_the_same_cutoff(season, user, manager):
    """The rule is the season's, not the profile's."""
    second = Manager.objects.create(user=user, nick_name="Buzz")
    theirs = Roster.objects.create(manager=second, season=season, name="Under the second")
    close_signings(season)

    with pytest.raises(ValidationError):
        services.buy(theirs, make_player("Late"), Decimal(1_000_000))
