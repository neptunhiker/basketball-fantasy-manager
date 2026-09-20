"""The reusable modal shell and TypedConfirmView, exercised through roster
deletion -- the first thing to use them.
"""

import datetime as dt

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.fantasy import services
from apps.fantasy.models import Roster, RosterPlayer, Season, Transaction
from apps.nba.models import Player

User = get_user_model()


@pytest.fixture
def season(db):
    return Season.objects.create(
        label="2026-27",
        starts_on=dt.date(2026, 10, 20),
        ends_on=dt.date(2027, 4, 11),
        is_current=True,
    )


@pytest.fixture
def signed_in(client, user, password, db):
    client.login(email=user.email, password=password)
    return client


@pytest.fixture
def roster(season, manager):
    roster = Roster.objects.create(manager=manager, season=season, name="Roster 1")
    player = Player.objects.create(
        first_name="Sold", last_name="Guy", position="G", current_salary=1_000_000
    )
    services.buy(roster, player, player.current_salary)
    return roster


def delete_url(roster):
    return reverse("fantasy:roster-delete", args=[roster.pk])


# --- the modal shell ---------------------------------------------------------


def test_get_renders_the_shell_with_the_confirmation_word(signed_in, roster):
    body = signed_in.get(delete_url(roster)).content.decode()

    assert 'role="dialog"' in body
    assert 'aria-modal="true"' in body
    assert "modal-backdrop" in body
    assert "DELETE" in body
    assert roster.name in body
    # The button starts disabled and only unlocks on an exact match.
    assert ":disabled=\"typed !== 'DELETE'\"" in body


def test_modal_shell_uses_the_saved_language(signed_in, roster, user):
    user.language = "de"
    user.save(update_fields=["language"])

    body = signed_in.get(delete_url(roster)).content.decode()

    assert "Schließen" in body
    assert "Abbrechen" in body
    assert "Gib" in body


def test_the_shell_names_what_will_be_lost(signed_in, roster):
    body = signed_in.get(delete_url(roster)).content.decode()
    assert "its 1 players" in body
    assert "cannot be undone" in body


def test_the_modal_posts_back_to_its_own_url(signed_in, roster):
    body = signed_in.get(delete_url(roster)).content.decode()
    assert f'hx-post="{delete_url(roster)}"' in body
    assert 'hx-target="#modal-root"' in body


def test_the_app_shell_has_somewhere_to_put_modals(signed_in, roster):
    body = signed_in.get(reverse("fantasy:roster-list")).content.decode()
    assert 'id="modal-root"' in body


# --- the confirmation gate ---------------------------------------------------


def test_the_wrong_word_does_not_delete(signed_in, roster):
    response = signed_in.post(delete_url(roster), {"confirm": "delete"})

    assert response.status_code == 200
    assert Roster.objects.filter(pk=roster.pk).exists()
    assert "exactly to confirm" in response.content.decode()


def test_an_empty_confirmation_does_not_delete(signed_in, roster):
    signed_in.post(delete_url(roster), {})
    assert Roster.objects.filter(pk=roster.pk).exists()


def test_the_error_response_is_swappable_by_htmx(signed_in, roster):
    """HTMX ignores non-2xx by default, so the corrected form must be a 200."""
    response = signed_in.post(delete_url(roster), {"confirm": "nope"})
    assert response.status_code == 200
    assert 'role="dialog"' in response.content.decode()


def test_the_right_word_deletes_and_redirects(signed_in, roster):
    response = signed_in.post(delete_url(roster), {"confirm": "DELETE"})

    assert response.status_code == 204
    assert response["HX-Redirect"] == reverse("fantasy:roster-list")
    assert not Roster.objects.filter(pk=roster.pk).exists()


def test_surrounding_whitespace_is_forgiven(signed_in, roster):
    signed_in.post(delete_url(roster), {"confirm": "  DELETE  "})
    assert not Roster.objects.filter(pk=roster.pk).exists()


def test_deleting_takes_memberships_and_transactions_with_it(signed_in, roster):
    assert RosterPlayer.objects.filter(roster=roster).exists()
    assert Transaction.objects.filter(roster=roster).exists()

    signed_in.post(delete_url(roster), {"confirm": "DELETE"})

    assert not RosterPlayer.objects.filter(roster=roster).exists()
    assert not Transaction.objects.filter(roster=roster).exists()
    # The player survives -- they were never ours to delete.
    assert Player.objects.filter(last_name="Guy").exists()


# --- ownership ---------------------------------------------------------------


def test_cannot_open_the_modal_for_someone_elses_roster(signed_in, season, other_manager):
    theirs = Roster.objects.create(manager=other_manager, season=season, name="Theirs")
    assert signed_in.get(delete_url(theirs)).status_code == 404


def test_cannot_delete_someone_elses_roster(signed_in, season, other_manager):
    theirs = Roster.objects.create(manager=other_manager, season=season, name="Theirs")

    assert signed_in.post(delete_url(theirs), {"confirm": "DELETE"}).status_code == 404
    assert Roster.objects.filter(pk=theirs.pk).exists()


def test_deleting_requires_login(client, roster):
    response = client.post(delete_url(roster), {"confirm": "DELETE"})
    assert response.status_code == 302
    assert Roster.objects.filter(pk=roster.pk).exists()
