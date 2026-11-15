"""Whether a roster counts on Analytics -> Insights: `Roster.is_active` and its switch."""

import datetime as dt
import json
from decimal import Decimal

import pytest
from django.urls import reverse

from apps.fantasy.models import Roster, Season


@pytest.fixture
def season(db):
    return Season.objects.create(
        label="2026-27",
        starts_on=dt.date(2026, 10, 21),
        ends_on=dt.date(2027, 4, 11),
        is_current=True,
        starting_cash=Decimal("60000000"),
    )


@pytest.fixture
def roster(season, manager):
    return Roster.objects.create(manager=manager, season=season, name="Bulla Ballers")


@pytest.fixture
def signed_in(client, user, password):
    client.login(email=user.email, password=password)
    return client


def active_url(roster):
    return reverse("fantasy:roster-active", args=[roster.pk])


def test_a_new_roster_is_active(roster):
    assert roster.is_active is True


def test_switching_off_hides_the_roster_and_redraws_its_card(signed_in, roster):
    response = signed_in.post(active_url(roster), {"active": "0"}, HTTP_HX_REQUEST="true")

    roster.refresh_from_db()
    assert roster.is_active is False
    assert response.status_code == 200
    assert f'id="roster-card-{roster.pk}"' in response.text
    assert 'aria-checked="false"' in response.text
    assert "Inactive" in response.text
    toast = json.loads(response["HX-Trigger-After-Swap"])["toast"]
    assert toast["message"] == "Bulla Ballers is hidden from Insights."


def test_switching_on_again(signed_in, roster):
    Roster.objects.filter(pk=roster.pk).update(is_active=False)

    response = signed_in.post(active_url(roster), {"active": "1"}, HTTP_HX_REQUEST="true")

    roster.refresh_from_db()
    assert roster.is_active is True
    assert 'aria-checked="true"' in response.text
    assert "Inactive" not in response.text


def test_the_switch_sets_a_state_rather_than_flipping_it(signed_in, roster):
    """A double click must not end up where the user started."""
    for _ in range(2):
        signed_in.post(active_url(roster), {"active": "0"}, HTTP_HX_REQUEST="true")

    roster.refresh_from_db()
    assert roster.is_active is False


def test_without_htmx_the_switch_goes_back_to_the_list(signed_in, roster):
    response = signed_in.post(active_url(roster), {"active": "0"})

    assert response.status_code == 302
    assert response.url == reverse("fantasy:roster-list")


def test_someone_elses_roster_cannot_be_switched(signed_in, season, other_manager):
    theirs = Roster.objects.create(manager=other_manager, season=season, name="Rivals")

    response = signed_in.post(active_url(theirs), {"active": "0"}, HTTP_HX_REQUEST="true")

    theirs.refresh_from_db()
    assert response.status_code == 404
    assert theirs.is_active is True


def test_signed_out_users_are_sent_to_log_in(client, roster):
    response = client.post(active_url(roster), {"active": "0"})

    roster.refresh_from_db()
    assert response.status_code == 302
    assert roster.is_active is True


def test_the_list_shows_the_switch_and_the_inactive_badge(signed_in, roster, season, manager):
    Roster.objects.create(manager=manager, season=season, name="What if", is_active=False)

    response = signed_in.get(reverse("fantasy:roster-list"))

    assert response.text.count('role="switch"') == 2
    assert response.text.count('aria-checked="true"') == 1
    assert response.text.count('aria-checked="false"') == 1
    assert response.text.count(">Inactive<") == 1
