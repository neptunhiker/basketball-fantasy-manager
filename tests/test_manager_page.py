"""The Managers page: seeing, adding, renaming and removing profiles.

Before this page existed a second profile could only come from the Django
admin or a shell, which made the roster dialog's manager picker unreachable in
practice. So the tests here care about the whole loop, and about two rules the
page is the first thing to enforce:

  * a handle is unique per account, case included -- two profiles called "Buzz"
    and "buzz" are the thing the uniqueness constraint exists to prevent;
  * a profile that still plays rosters cannot be deleted, because
    `Roster.manager` cascades and would take the transaction history with it.
"""

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.fantasy import services
from apps.fantasy.models import Manager, Roster, Season
from apps.nba.models import Player

pytestmark = pytest.mark.django_db

LIST = reverse("fantasy:manager-list")
CREATE = reverse("fantasy:manager-create")


@pytest.fixture
def season(db):
    # Local, like the other nine test modules that need one. A shared fixture
    # would be an improvement and a nine-file change; not this change.
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


def rename_url(profile):
    return reverse("fantasy:manager-rename", args=[profile.pk])


def delete_url(profile):
    return reverse("fantasy:manager-delete", args=[profile.pk])


# --- the list ----------------------------------------------------------------


def test_the_page_needs_a_login(client):
    response = client.get(LIST)

    assert response.status_code == 302
    assert reverse("accounts:login") in response["Location"]


def test_the_page_lists_your_profiles(signed_in, user, manager):
    Manager.objects.create(user=user, nick_name="Buzz")

    body = signed_in.get(LIST).content.decode()

    assert manager.nick_name in body
    assert "Buzz" in body


def test_the_page_does_not_list_another_accounts_profiles(signed_in, other_manager):
    assert other_manager.nick_name not in signed_in.get(LIST).content.decode()


def test_each_profile_shows_its_rosters(signed_in, manager, season):
    Roster.objects.create(manager=manager, season=season, name="Bulla Ballers")

    body = signed_in.get(LIST).content.decode()

    assert "Bulla Ballers" in body
    assert reverse("fantasy:roster-build", args=[manager.rosters.get().pk]) in body


def test_rosters_are_grouped_under_their_season_labels(signed_in, manager):
    current = Season.objects.create(
        label="2026-27",
        starts_on="2026-10-01",
        ends_on="2027-04-30",
        is_current=True,
    )
    earlier = Season.objects.create(
        label="2025-26",
        starts_on="2025-10-01",
        ends_on="2026-04-30",
        is_current=False,
    )
    Roster.objects.create(manager=manager, season=current, name="Prime Team")
    Roster.objects.create(manager=manager, season=earlier, name="Legacy Team")

    body = signed_in.get(LIST).content.decode()

    assert "2026-27" in body
    assert "2025-26" in body
    assert "Prime Team" in body
    assert "Legacy Team" in body


def test_each_roster_card_shows_icon_cash_team_value_and_player_count(signed_in, manager):
    open_signings = Season.objects.create(
        label="2027-28",
        starts_on="2027-10-01",
        ends_on="2028-04-30",
        is_current=False,
    )
    roster = Roster.objects.create(
        manager=manager,
        season=open_signings,
        name="Bulla Ballers",
        icon="lion",
    )
    player = Player.objects.create(
        first_name="Jalen",
        last_name="Brunson",
        position="G",
        current_salary=Decimal("9600000"),
    )
    services.buy(roster, player, player.current_salary)

    body = signed_in.get(LIST).content.decode()

    assert "img/roster_icons/lion.png" in body
    assert "$50.40M" in body  # cash after a 9.60M signing
    assert "$9.60M" in body  # squad value from one signed player
    assert "Players" in body
    assert "1 / 15" in body
    assert "Jalen Brunson" not in body  # individual players are no longer listed


def test_a_profile_with_no_rosters_says_so(signed_in, manager):
    assert "No rosters yet." in signed_in.get(LIST).content.decode()


def test_an_account_with_no_profiles_gets_an_empty_state(signed_in):
    body = signed_in.get(LIST).content.decode()

    assert "No profiles yet." in body
    assert CREATE in body


def test_there_is_a_link_in_the_sidebar(signed_in):
    assert LIST in signed_in.get(reverse("fantasy:roster-list")).content.decode()


# --- adding ------------------------------------------------------------------


def test_the_dialog_opens_empty(signed_in, user):
    """No suggested handle: by the time anyone opens this, the one the account
    would suggest is usually already taken by the first profile."""
    body = signed_in.get(CREATE).content.decode()

    assert 'value=""' in body
    assert user.manager_profiles.count() == 0


def test_a_profile_can_be_created(signed_in, user):
    response = signed_in.post(CREATE, {"nick_name": "Buzz"}, HTTP_HX_REQUEST="true")

    assert response.status_code == 204
    assert response["HX-Redirect"] == LIST
    assert list(user.manager_profiles.values_list("nick_name", flat=True)) == ["Buzz"]


def test_a_second_profile_can_be_created(signed_in, user, manager):
    """The point of the page: the roster dialog's picker needs two to appear."""
    signed_in.post(CREATE, {"nick_name": "Buzz"}, HTTP_HX_REQUEST="true")

    assert user.manager_profiles.count() == 2


def test_a_profile_lands_on_your_own_account(signed_in, user, other_manager):
    signed_in.post(CREATE, {"nick_name": "Buzz"}, HTTP_HX_REQUEST="true")

    assert Manager.objects.get(nick_name="Buzz").user == user
    assert other_manager.user.manager_profiles.count() == 1


def test_a_blank_nickname_is_refused(signed_in, user):
    response = signed_in.post(CREATE, {"nick_name": "   "}, HTTP_HX_REQUEST="true")

    assert response.status_code == 200
    assert "Please enter a name." in response.content.decode()
    assert user.manager_profiles.count() == 0


def test_a_nickname_you_already_use_is_refused(signed_in, user, manager):
    response = signed_in.post(CREATE, {"nick_name": manager.nick_name}, HTTP_HX_REQUEST="true")

    assert "already have a profile called" in response.content.decode()
    assert user.manager_profiles.count() == 1


def test_a_nickname_differing_only_in_case_is_refused(signed_in, user, manager):
    """Stricter than `unique_nickname_per_user`, which compares exactly. The
    constraint exists so two profiles can be told apart on screen, and
    "Bulla" beside "bulla" is that problem."""
    response = signed_in.post(
        CREATE, {"nick_name": manager.nick_name.lower()}, HTTP_HX_REQUEST="true"
    )

    assert "already have a profile called" in response.content.decode()
    assert user.manager_profiles.count() == 1


def test_another_accounts_nickname_is_free(signed_in, user, other_manager):
    """Uniqueness is per account. Two people picking the same handle is not a
    clash -- nothing shows them side by side."""
    signed_in.post(CREATE, {"nick_name": other_manager.nick_name}, HTTP_HX_REQUEST="true")

    assert user.manager_profiles.filter(nick_name=other_manager.nick_name).exists()


def test_a_long_nickname_is_cut_to_what_the_column_holds(signed_in, user):
    signed_in.post(CREATE, {"nick_name": "B" * 60}, HTTP_HX_REQUEST="true")

    assert user.manager_profiles.get().nick_name == "B" * 40


# --- renaming ----------------------------------------------------------------


def test_the_rename_dialog_is_prefilled(signed_in, manager):
    assert f'value="{manager.nick_name}"' in signed_in.get(rename_url(manager)).content.decode()


def test_a_profile_can_be_renamed(signed_in, manager):
    response = signed_in.post(rename_url(manager), {"nick_name": "Buzz"}, HTTP_HX_REQUEST="true")

    assert response.status_code == 204
    manager.refresh_from_db()
    assert manager.nick_name == "Buzz"


def test_renaming_does_not_move_the_rosters(signed_in, manager, season):
    roster = Roster.objects.create(manager=manager, season=season, name="Bulla Ballers")

    signed_in.post(rename_url(manager), {"nick_name": "Buzz"}, HTTP_HX_REQUEST="true")

    roster.refresh_from_db()
    assert roster.manager == manager


def test_keeping_the_same_nickname_is_not_a_clash(signed_in, manager):
    """The obvious way to write the uniqueness check refuses this."""
    response = signed_in.post(
        rename_url(manager), {"nick_name": manager.nick_name}, HTTP_HX_REQUEST="true"
    )

    assert response.status_code == 204


def test_renaming_onto_your_other_profile_is_refused(signed_in, user, manager):
    other = Manager.objects.create(user=user, nick_name="Buzz")

    response = signed_in.post(
        rename_url(other), {"nick_name": manager.nick_name}, HTTP_HX_REQUEST="true"
    )

    assert "already have a profile called" in response.content.decode()
    other.refresh_from_db()
    assert other.nick_name == "Buzz"


def test_another_accounts_profile_cannot_be_renamed(signed_in, other_manager):
    response = signed_in.post(
        rename_url(other_manager), {"nick_name": "Mine now"}, HTTP_HX_REQUEST="true"
    )

    assert response.status_code == 404
    other_manager.refresh_from_db()
    assert other_manager.nick_name == "Rival"


# --- deleting ----------------------------------------------------------------


def test_an_empty_profile_can_be_deleted(signed_in, user, manager):
    response = signed_in.post(delete_url(manager), {}, HTTP_HX_REQUEST="true")

    assert response.status_code == 204
    assert response["HX-Redirect"] == LIST
    assert user.manager_profiles.count() == 0


def test_deleting_an_empty_profile_needs_no_word_typed(signed_in, manager):
    """A profile that may be deleted holds nothing, so typing DELETE would be
    friction guarding nothing -- and it teaches people not to read."""
    body = signed_in.get(delete_url(manager)).content.decode()

    assert "to confirm" not in body
    assert "Delete profile" in body


def test_a_profile_holding_a_roster_cannot_be_deleted(signed_in, user, manager, season):
    Roster.objects.create(manager=manager, season=season, name="Bulla Ballers")

    response = signed_in.post(delete_url(manager), {}, HTTP_HX_REQUEST="true")

    assert response.status_code == 200
    assert user.manager_profiles.count() == 1
    assert Roster.objects.count() == 1


def test_the_dialog_explains_why_and_offers_no_button(signed_in, manager, season):
    Roster.objects.create(manager=manager, season=season, name="Bulla Ballers")

    body = signed_in.get(delete_url(manager)).content.decode()

    assert "Bulla Ballers" in body
    assert "transaction history" in body
    # The modal is an explanation, not a dare.
    assert "Delete profile" not in body
    assert "Close" in body


def test_another_accounts_profile_cannot_be_deleted(signed_in, other_manager):
    response = signed_in.post(delete_url(other_manager), {}, HTTP_HX_REQUEST="true")

    assert response.status_code == 404
    assert Manager.objects.filter(pk=other_manager.pk).exists()


# --- the loop the page exists to close ---------------------------------------


def test_a_profile_made_here_can_be_picked_in_the_roster_dialog(signed_in, manager, season):
    """End to end: the picker only appears with two profiles, and until this
    page existed a second one could not be made through the app at all."""
    signed_in.post(CREATE, {"nick_name": "Buzz"}, HTTP_HX_REQUEST="true")

    body = signed_in.get(reverse("fantasy:roster-create")).content.decode()

    assert "<select" in body
    assert "Buzz" in body
    assert manager.nick_name in body
