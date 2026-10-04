import datetime as dt

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.fantasy.models import Manager, Roster, Season

pytestmark = pytest.mark.django_db

User = get_user_model()
LIST = reverse("fantasy:roster-list")


@pytest.fixture
def season():
    return Season.objects.create(
        label="2026-27",
        starts_on=dt.date(2026, 10, 20),
        ends_on=dt.date(2027, 4, 11),
        is_current=True,
    )


@pytest.fixture
def rosters(user, other_manager, season):
    own_manager = Manager.objects.create(user=user, nick_name="Own")
    own = Roster.objects.create(manager=own_manager, season=season, name="Own roster")
    other = Roster.objects.create(manager=other_manager, season=season, name="Other roster")
    return own, other


def login(client, user, password):
    client.login(email=user.email, password=password)
    return client


def roster_names(response):
    return {roster.name for roster in response.context["rosters"]}


def test_a_user_sees_only_their_own_rosters(client, user, password, rosters):
    response = login(client, user, password).get(LIST)

    assert response.status_code == 200
    assert roster_names(response) == {"Own roster"}


# Staff see other managers' rosters on the Managers pages. "My rosters" lists
# only rosters that can be opened, and every roster page is owner-only.


def test_a_staff_user_sees_only_their_own_rosters(client, staff_user, password, rosters, season):
    mine = Manager.objects.create(user=staff_user, nick_name="Staff")
    Roster.objects.create(manager=mine, season=season, name="Staff roster")

    response = login(client, staff_user, password).get(LIST)

    assert response.status_code == 200
    assert roster_names(response) == {"Staff roster"}


def test_a_superuser_sees_only_their_own_rosters(client, password, rosters):
    superuser = User.objects.create_superuser(email="admin@example.com", password=password)
    response = login(client, superuser, password).get(LIST)

    assert response.status_code == 200
    assert roster_names(response) == set()
