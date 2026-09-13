"""Naming a roster on creation -- the prompt modal and PromptView, exercised
through the first thing to use them.
"""

import datetime as dt

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.fantasy.models import ROSTER_ICON_CHOICES, Roster, Season

User = get_user_model()

CREATE_URL = "/rosters/new/"


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


def create_url():
    url = reverse("fantasy:roster-create")
    assert url == CREATE_URL
    return url


# --- the prompt --------------------------------------------------------------


def test_the_list_page_offers_profile_creation_before_a_roster(signed_in, season):
    body = signed_in.get(reverse("fantasy:roster-list")).content.decode()

    assert f'hx-get="{create_url()}"' in body
    assert 'hx-target="#modal-root"' in body
    assert not Roster.objects.exists()


def test_the_prompt_asks_for_a_name(signed_in, season, manager):
    body = signed_in.get(create_url()).content.decode()

    assert 'role="dialog"' in body
    assert "New roster" in body
    assert 'name="name"' in body
    assert "Create roster" in body
    # Nothing is created just by looking at the form.
    assert not Roster.objects.exists()


def test_the_prompt_suggests_a_name_so_enter_is_enough(signed_in, season, manager):
    body = signed_in.get(create_url()).content.decode()
    assert 'value="Roster 1"' in body


def test_the_suggestion_skips_names_already_taken(signed_in, season, manager):
    Roster.objects.create(manager=manager, season=season, name="Roster 1")
    body = signed_in.get(create_url()).content.decode()
    assert 'value="Roster 2"' in body


def test_the_prompt_posts_back_to_its_own_url(signed_in, season, manager):
    body = signed_in.get(create_url()).content.decode()
    assert f'hx-post="{create_url()}"' in body


def test_the_prompt_shows_every_roster_icon(signed_in, season, manager):
    body = signed_in.get(create_url()).content.decode()

    for icon, label in ROSTER_ICON_CHOICES:
        assert f'name="icon" value="{icon}"' in body
        assert f'alt="{label}"' in body
        assert f"img/roster_icons/{icon}.png" in body


# --- what gets accepted ------------------------------------------------------


def test_the_typed_name_wins_over_the_suggestion(signed_in, season, manager):
    signed_in.post(create_url(), {"name": "Bulla Ballers"})
    assert Roster.objects.get().name == "Bulla Ballers"


def test_the_selected_icon_is_saved(signed_in, season, manager):
    signed_in.post(create_url(), {"name": "Bulla Ballers", "icon": "sonic"})

    assert Roster.objects.get().icon == "sonic"


def test_surrounding_whitespace_is_trimmed(signed_in, season, manager):
    signed_in.post(create_url(), {"name": "  Bulla Ballers  "})
    assert Roster.objects.get().name == "Bulla Ballers"


def test_an_empty_name_creates_nothing(signed_in, season, manager):
    response = signed_in.post(create_url(), {"name": "   "})

    assert response.status_code == 200
    assert not Roster.objects.exists()
    assert "Please enter a name." in response.content.decode()


def test_a_missing_name_creates_nothing(signed_in, season, manager):
    signed_in.post(create_url(), {})
    assert not Roster.objects.exists()


def test_the_error_response_is_swappable_by_htmx(signed_in, season, manager):
    """HTMX ignores non-2xx by default, so the corrected form must be a 200."""
    response = signed_in.post(create_url(), {"name": ""})

    assert response.status_code == 200
    assert 'role="dialog"' in response.content.decode()


def test_a_rejected_name_is_echoed_back_instead_of_retyped(signed_in, season, manager):
    Roster.objects.create(manager=manager, season=season, name="Bulla Ballers")
    response = signed_in.post(create_url(), {"name": "Bulla Ballers"})

    assert response.status_code == 200
    assert 'value="Bulla Ballers"' in response.content.decode()


def test_a_name_used_twice_in_a_season_is_refused_with_a_sentence(signed_in, season, manager):
    Roster.objects.create(manager=manager, season=season, name="Bulla Ballers")
    response = signed_in.post(create_url(), {"name": "Bulla Ballers"})

    assert response.status_code == 200
    assert "already have a roster called" in response.content.decode()
    assert Roster.objects.count() == 1


def test_someone_elses_name_is_not_in_the_way(signed_in, season, manager, other_manager):
    Roster.objects.create(manager=other_manager, season=season, name="Bulla Ballers")

    signed_in.post(create_url(), {"name": "Bulla Ballers"})

    assert Roster.objects.filter(name="Bulla Ballers").count() == 2


def test_an_overlong_name_is_cut_to_the_column_width(signed_in, season, manager):
    signed_in.post(create_url(), {"name": "B" * 200})
    assert len(Roster.objects.get().name) == 80


# --- the success handoff -----------------------------------------------------


def test_an_htmx_post_redirects_via_the_header(signed_in, season, manager):
    response = signed_in.post(create_url(), {"name": "Bulla Ballers"}, HTTP_HX_REQUEST="true")
    roster = Roster.objects.get()

    assert response.status_code == 204
    assert response["HX-Redirect"] == reverse("fantasy:roster-rules", args=[roster.pk])


def test_a_plain_post_still_redirects(signed_in, season, manager):
    """The prompt is enhancement: without HTMX the form must still work."""
    response = signed_in.post(create_url(), {"name": "Bulla Ballers"})
    roster = Roster.objects.get()

    assert response.status_code == 302
    assert response.url == reverse("fantasy:roster-rules", args=[roster.pk])


# --- guards ------------------------------------------------------------------


def test_the_prompt_needs_a_current_season(signed_in, db):
    response = signed_in.get(create_url())
    assert response.status_code == 409
    assert "No season is marked as current" in response.content.decode()


def test_naming_requires_login(client, season):
    response = client.post(CREATE_URL, {"name": "Bulla Ballers"})
    assert response.status_code == 302
    assert not Roster.objects.exists()


# --- the suggestion ----------------------------------------------------------


def test_the_suggestion_offers_the_lowest_free_number(signed_in, season, manager):
    """A hand-named roster does not push the suggestion up past Roster 1."""
    Roster.objects.create(manager=manager, season=season, name="Bulla Ballers")
    body = signed_in.get(create_url()).content.decode()
    assert 'value="Roster 1"' in body


def test_the_suggestion_fills_a_gap(signed_in, season, manager):
    Roster.objects.create(manager=manager, season=season, name="Roster 2")
    body = signed_in.get(create_url()).content.decode()
    assert 'value="Roster 1"' in body
