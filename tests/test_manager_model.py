"""The layer between an account and its rosters.

Two things are worth pinning down here, because both are decisions rather than
consequences and both would go quietly wrong.

A `Manager` is a *way of playing*, not a person: one account may keep several,
which is why the link is a ForeignKey and not a OneToOne. Nothing in the app
can create a second one yet, so only a test would notice if that link tightened
by accident.

And a manager is someone who manages a roster, so a profile is not supposed to
appear until there is a roster for it to hold. That makes merely opening the
"new roster" dialog and cancelling out of it something the tests have to watch:
it is a GET, and a GET that writes leaves debris behind for everyone who
changes their mind.
"""

import pytest
from django.db import connection
from django.db.utils import IntegrityError
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.fantasy import services
from apps.fantasy.models import Manager, Roster, Season

pytestmark = pytest.mark.django_db


@pytest.fixture
def season(db):
    return Season.objects.create(
        label="2025-26",
        starts_on="2025-10-01",
        ends_on="2026-04-30",
        is_current=True,
    )


@pytest.fixture
def signed_in(client, user, password):
    client.login(email=user.email, password=password)
    return client


def create_url():
    return reverse("fantasy:roster-create")


# --- the model ---------------------------------------------------------------


def test_a_manager_is_named_by_its_handle(manager):
    assert str(manager) == "Bulla"


def test_display_name_falls_back_to_the_account(user):
    """An empty handle should still render as something, not as a blank cell."""
    nameless = Manager(user=user, nick_name="")
    assert nameless.display_name == user.display_name


def test_one_user_may_keep_several_profiles(user, manager):
    """The whole reason `user` is a ForeignKey and not a OneToOneField.

    Nothing in the app creates a second profile today, so this is the only
    place that would notice the link being tightened.
    """
    second = Manager.objects.create(user=user, nick_name="Bulla B")

    assert second.pk != manager.pk
    assert set(user.manager_profiles.values_list("nick_name", flat=True)) == {
        "Bulla",
        "Bulla B",
    }


def test_a_handle_cannot_repeat_within_one_account(user, manager):
    with pytest.raises(IntegrityError):
        Manager.objects.create(user=user, nick_name=manager.nick_name)


def test_two_accounts_may_share_a_handle(manager, other_manager):
    """Uniqueness is per account, not global: two people called Bulla is fine."""
    other_manager.nick_name = manager.nick_name
    other_manager.save()

    assert Manager.objects.filter(nick_name="Bulla").count() == 2


def test_deleting_an_account_takes_its_managers_and_rosters(user, manager, season):
    Roster.objects.create(manager=manager, season=season, name="Roster 1")

    user.delete()

    assert Manager.objects.count() == 0
    assert Roster.objects.count() == 0


# --- picking one -------------------------------------------------------------


def test_a_user_with_no_profile_has_no_manager(user):
    assert services.manager_for(user) is None


def test_manager_for_does_not_create_one(user):
    """The half that runs on a GET. It has to be a read, and only a read."""
    services.manager_for(user)

    assert Manager.objects.count() == 0


def test_ensure_manager_creates_the_first_profile(user):
    manager = services.ensure_manager(user)

    assert manager.user == user
    assert Manager.objects.count() == 1


def test_ensure_manager_is_idempotent(user):
    first = services.ensure_manager(user)
    second = services.ensure_manager(user)

    assert first == second
    assert Manager.objects.count() == 1


def test_ensure_manager_keeps_the_oldest_profile(user, manager):
    Manager.objects.create(user=user, nick_name="Later")

    assert services.ensure_manager(user) == manager


def test_the_suggested_handle_comes_from_the_account(user):
    user.first_name = "Sebastian"

    assert services.default_nickname(user) == "Sebastian"


def test_the_suggested_handle_falls_back_to_the_email(user):
    """An invited account has no name yet, and still has to be able to play."""
    assert user.first_name == ""
    assert services.default_nickname(user) == user.email.split("@")[0]


def test_the_suggested_handle_fits_the_column(user):
    user.first_name = "x" * 200

    assert len(services.default_nickname(user)) == 40


# --- the create flow ---------------------------------------------------------


def test_opening_the_dialog_creates_no_profile(signed_in, season):
    """Cancelling out of "New roster" must not leave a manager behind."""
    assert signed_in.get(create_url()).status_code == 200
    assert Manager.objects.count() == 0


def test_the_first_roster_brings_a_profile_with_it(signed_in, user, season):
    signed_in.post(create_url(), {"name": "Bulla Ballers"})

    manager = Manager.objects.get()
    assert manager.user == user
    assert list(manager.rosters.values_list("name", flat=True)) == ["Bulla Ballers"]


def test_a_second_roster_reuses_the_same_profile(signed_in, season):
    signed_in.post(create_url(), {"name": "First"})
    signed_in.post(create_url(), {"name": "Second"})

    assert Manager.objects.count() == 1
    assert Manager.objects.get().rosters.count() == 2


def test_the_suggestion_starts_at_one_before_any_profile_exists(signed_in, season):
    """`default_roster_name(None, ...)`: no profile means no rosters, so all free."""
    body = signed_in.get(create_url()).content.decode()

    assert 'value="Roster 1"' in body


# --- what the scoping rule actually scopes to --------------------------------


def test_a_roster_under_your_second_profile_is_still_yours(signed_in, user, manager, season):
    """The rule is per account, not per profile.

    `OwnRosterMixin` matches `manager__user`, so a roster held by any of your
    own profiles opens. Scoping to one profile would make the answer depend on
    which one you were "viewing as" -- a notion the app does not have.
    """
    second = Manager.objects.create(user=user, nick_name="Bulla B")
    theirs = Roster.objects.create(manager=second, season=season, name="Under the other one")

    url = reverse("fantasy:roster-build", args=[theirs.pk])
    assert signed_in.get(url).status_code == 200


def test_another_accounts_roster_is_still_hidden(signed_in, other_manager, season):
    theirs = Roster.objects.create(manager=other_manager, season=season, name="Theirs")

    url = reverse("fantasy:roster-build", args=[theirs.pk])
    assert signed_in.get(url).status_code == 404


def test_the_list_shows_every_profiles_rosters(signed_in, user, manager, season):
    second = Manager.objects.create(user=user, nick_name="Bulla B")
    Roster.objects.create(manager=manager, season=season, name="Under the first")
    Roster.objects.create(manager=second, season=season, name="Under the second")

    body = signed_in.get(reverse("fantasy:roster-list")).content.decode()

    assert "Under the first" in body
    assert "Under the second" in body


def test_the_cards_state_which_profile_holds_each_roster(signed_in, user, manager, season):
    """Two profiles, two rosters, so each card has to name its own.

    One profile would let a card print any handle it found and still pass.
    Nicknames chosen so neither is a prefix of the other, which would make the
    two assertions below agree with each other by accident.
    """
    second = Manager.objects.create(user=user, nick_name="Buzz")
    Roster.objects.create(manager=manager, season=season, name="Under the first")
    Roster.objects.create(manager=second, season=season, name="Under the second")

    body = signed_in.get(reverse("fantasy:roster-list")).content.decode()
    cards = {
        name: body.split(name)[1].split("</a>")[0]
        for name in ("Under the first", "Under the second")
    }

    assert "Managed by Bulla" in cards["Under the first"]
    assert "Managed by Buzz" not in cards["Under the first"]
    assert "Managed by Buzz" in cards["Under the second"]


def test_the_card_keeps_the_season_beside_the_handle(signed_in, manager, season):
    """Both on the subtitle, which is the point: the card gained no row."""
    Roster.objects.create(manager=manager, season=season, name="Solo")

    body = signed_in.get(reverse("fantasy:roster-list")).content.decode()
    subtitle = body.split("Solo")[1].split("</span>")[1]

    assert season.label in subtitle
    assert "Managed by Bulla" in subtitle


def test_the_cards_cost_no_query_per_roster(signed_in, user, manager, season):
    """`RosterListView` select_relates the manager, and the cards rely on it.

    Three rosters under three profiles: without the join this is three extra
    statements, which is the N+1 the assertion is really about.
    """
    for index in range(2):
        other = Manager.objects.create(user=user, nick_name=f"Extra {index}")
        Roster.objects.create(manager=other, season=season, name=f"Roster {index}")
    Roster.objects.create(manager=manager, season=season, name="Mine")

    with CaptureQueriesContext(connection) as captured:
        signed_in.get(reverse("fantasy:roster-list"))

    lazy = [
        query["sql"]
        for query in captured.captured_queries
        if "fantasy_manager" in query["sql"] and "fantasy_roster" not in query["sql"]
    ]
    assert lazy == []


def test_the_same_name_may_be_used_by_each_of_your_profiles(user, manager, season):
    """Uniqueness moved from the account to the profile. This is that change."""
    second = Manager.objects.create(user=user, nick_name="Bulla B")

    Roster.objects.create(manager=manager, season=season, name="Roster 1")
    Roster.objects.create(manager=second, season=season, name="Roster 1")

    assert Roster.objects.filter(name="Roster 1").count() == 2


# --- picking the profile in the modal ----------------------------------------


def test_no_picker_when_there_is_nothing_to_pick(signed_in, manager, season):
    """One profile is not a choice, so the modal stays a single field."""
    body = signed_in.get(create_url()).content.decode()

    assert 'name="manager"' not in body
    assert "<select" not in body


def test_no_picker_before_the_first_profile_exists(signed_in, season):
    body = signed_in.get(create_url()).content.decode()

    assert "<select" not in body


def test_the_picker_lists_every_profile(signed_in, user, manager, season):
    second = Manager.objects.create(user=user, nick_name="Basti")

    body = signed_in.get(create_url()).content.decode()

    assert 'name="manager"' in body
    assert f'value="{manager.pk}"' in body
    assert f'value="{second.pk}"' in body


def test_the_picker_does_not_offer_another_accounts_profile(
    signed_in, user, manager, other_manager, season
):
    Manager.objects.create(user=user, nick_name="Basti")

    body = signed_in.get(create_url()).content.decode()

    assert str(other_manager.pk) not in body


def test_the_picker_starts_on_the_profile_a_roster_would_have_used(
    signed_in, user, manager, season
):
    """Listed by name, preselected by age -- so `Basti` sorts first but `Bulla`
    is the one already selected, matching `manager_for`."""
    Manager.objects.create(user=user, nick_name="Basti")

    body = signed_in.get(create_url()).content.decode()
    selected = body.split(f'value="{manager.pk}"')[1].split(">")[0]

    assert "selected" in selected


def test_the_roster_lands_under_the_chosen_profile(signed_in, user, manager, season):
    second = Manager.objects.create(user=user, nick_name="Basti")

    signed_in.post(create_url(), {"name": "Second squad", "manager": str(second.pk)})

    assert list(second.rosters.values_list("name", flat=True)) == ["Second squad"]
    assert manager.rosters.count() == 0


def test_a_missing_choice_falls_back_to_the_usual_profile(signed_in, manager, season):
    """No select is rendered for one profile, so the POST carries no manager."""
    signed_in.post(create_url(), {"name": "Only squad"})

    assert list(manager.rosters.values_list("name", flat=True)) == ["Only squad"]


def test_another_accounts_profile_cannot_be_posted_in(signed_in, manager, other_manager, season):
    """The rendered options bind the browser and nothing else.

    A hand-written POST is the reason `clean_choice` looks the profile up
    through `manager_profiles` instead of `Manager.objects`.
    """
    response = signed_in.post(create_url(), {"name": "Not yours", "manager": str(other_manager.pk)})

    assert response.status_code == 200
    assert "Pick one of your manager profiles." in response.content.decode()
    assert Roster.objects.count() == 0


def test_a_nonsense_choice_is_refused_rather_than_crashing(signed_in, manager, season):
    """`pk="banana"` reaches a UUID column, which raises before it can miss."""
    response = signed_in.post(create_url(), {"name": "Whatever", "manager": "banana"})

    assert response.status_code == 200
    assert "Pick one of your manager profiles." in response.content.decode()
    assert Roster.objects.count() == 0


def test_the_same_name_under_two_profiles_is_accepted_through_the_modal(
    signed_in, user, manager, season
):
    second = Manager.objects.create(user=user, nick_name="Basti")
    Roster.objects.create(manager=manager, season=season, name="Roster 1")

    signed_in.post(create_url(), {"name": "Roster 1", "manager": str(second.pk)})

    assert Roster.objects.filter(name="Roster 1").count() == 2


def test_a_clash_names_the_profile_when_one_was_chosen(signed_in, user, manager, season):
    """With a picker on screen, "you already have" would point at the wrong
    thing -- the clash belongs to one profile, not to the account."""
    Manager.objects.create(user=user, nick_name="Basti")
    Roster.objects.create(manager=manager, season=season, name="Roster 1")

    response = signed_in.post(create_url(), {"name": "Roster 1", "manager": str(manager.pk)})
    body = response.content.decode()

    # The quotes around the name come back HTML-escaped, so the assertion
    # stops at the profile -- which is the part this test is about.
    assert f"{manager.nick_name} already has a roster called" in body
    assert "You already have" not in body
    assert Roster.objects.count() == 1


def test_a_rejected_name_keeps_the_chosen_profile(signed_in, user, manager, season):
    """Fixing the name should not also mean re-picking the profile."""
    second = Manager.objects.create(user=user, nick_name="Basti")
    Roster.objects.create(manager=second, season=season, name="Roster 1")

    body = signed_in.post(
        create_url(), {"name": "Roster 1", "manager": str(second.pk)}
    ).content.decode()
    selected = body.split(f'value="{second.pk}"')[1].split(">")[0]

    assert "selected" in selected
