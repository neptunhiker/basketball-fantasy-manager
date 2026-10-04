"""Superusers giving other accounts a role: Coach, Team or Administration."""

import re

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from apps.accounts.models import Role
from apps.accounts.services import change_role

User = get_user_model()


@pytest.fixture
def admin(db, password):
    return User.objects.create_user(
        email="boss@example.com", password=password, is_staff=True, is_superuser=True
    )


@pytest.fixture
def admin_client(client, admin, password):
    client.login(email=admin.email, password=password)
    return client


def role_url(person):
    return reverse("accounts:user-role", args=[person.pk])


# --- the rules --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "staff", "superuser"),
    [(Role.COACH, False, False), (Role.TEAM, True, False), (Role.ADMIN, True, True)],
)
def test_a_role_sets_both_flags(admin, user, role, staff, superuser):
    change_role(admin, user, role)

    user.refresh_from_db()
    assert (user.is_staff, user.is_superuser) == (staff, superuser)
    assert user.role == role


def test_only_superusers_change_roles(staff_user, user):
    with pytest.raises(PermissionDenied):
        change_role(staff_user, user, Role.TEAM)


def test_nobody_changes_their_own_role(admin):
    with pytest.raises(ValidationError, match="your own role"):
        change_role(admin, admin, Role.COACH)


def test_the_last_administrator_keeps_the_role(admin, password):
    # A second superuser who is not active does not count as somebody in charge.
    other = User.objects.create_user(
        email="gone@example.com", password=password, is_superuser=True, is_active=False
    )
    acting = User.objects.create_user(email="acting@example.com", password=password)
    acting.is_superuser = True  # in memory only: a stale session, say

    with pytest.raises(ValidationError, match="last administrator"):
        change_role(acting, admin, Role.TEAM)
    admin.refresh_from_db()
    assert admin.is_superuser
    assert not other.is_active


def test_one_administrator_may_demote_another(admin, password):
    second = User.objects.create_user(
        email="second@example.com", password=password, is_staff=True, is_superuser=True
    )

    change_role(admin, second, Role.TEAM)

    second.refresh_from_db()
    assert (second.is_staff, second.is_superuser) == (True, False)


# --- the screens ------------------------------------------------------------------


def test_an_admin_sees_change_role_on_someone_else(admin_client, user):
    body = admin_client.get(reverse("accounts:user-detail", args=[user.pk])).text

    assert "Change role" in body
    assert role_url(user) in body


def test_there_is_no_change_role_on_your_own_page(admin_client, admin):
    body = admin_client.get(reverse("accounts:user-detail", args=[admin.pk])).text

    assert "Change role" not in body


def test_staff_who_are_not_superusers_cannot_change_roles(client, staff_user, user, password):
    client.login(email=staff_user.email, password=password)

    assert "Change role" not in client.get(reverse("accounts:user-detail", args=[user.pk])).text
    assert client.get(role_url(user)).status_code == 403
    assert client.post(role_url(user), {"role": "admin"}).status_code == 403
    user.refresh_from_db()
    assert not user.is_superuser


def test_the_modal_offers_the_three_roles_with_the_current_one_ticked(admin_client, staff_user):
    body = admin_client.get(role_url(staff_user)).text

    for label in ("Coach", "Team", "Administration"):
        assert label in body
    checked = re.findall(r'value="(\w+)"\s+checked', body)
    assert checked == ["team"]
    assert "import player data from basketball.de" in body


def test_making_someone_team_lets_them_import(admin_client, client, user, password):
    response = admin_client.post(role_url(user), {"role": "team"}, headers={"HX-Request": "true"})

    assert response.status_code == 204
    assert response["HX-Redirect"] == reverse("accounts:user-detail", args=[user.pk])
    user.refresh_from_db()
    assert user.is_staff and not user.is_superuser

    client.logout()
    client.login(email=user.email, password=password)
    assert client.get(reverse("nba:bbde-import")).status_code == 200


def test_a_refused_change_says_why_in_the_modal(admin_client, admin):
    response = admin_client.post(role_url(admin), {"role": "coach"})

    assert response.status_code == 200
    assert "You cannot change your own role." in response.text
    admin.refresh_from_db()
    assert admin.is_superuser


def test_an_unknown_role_is_refused(admin_client, user):
    response = admin_client.post(role_url(user), {"role": "emperor"})

    assert response.status_code == 200
    user.refresh_from_db()
    assert not user.is_staff
