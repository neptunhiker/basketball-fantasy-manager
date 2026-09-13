"""What the sidebar says about who is signed in.

The card used to print the email twice -- once as `display_name`, which falls
back to the address when an account has no name, and once as the address. Two
lines, one fact, and neither of them the person's name.

So the card now shows one thing: a shortened name. These tests hold that
shape -- the identity is named rather than addressed, and nothing else shares
the line. The manager nicknames were listed here for a while and are not any
more; `test_the_card_does_not_list_the_manager_profiles` is what keeps them
from creeping back.
"""

import pytest
from django.urls import reverse

from apps.fantasy.models import Manager

pytestmark = pytest.mark.django_db


@pytest.fixture
def signed_in(client, user, password):
    client.login(email=user.email, password=password)
    return client


def sidebar(client):
    """Any page inside the shell renders it; the roster list needs no season."""
    return client.get(reverse("fantasy:roster-list")).content.decode()


def test_the_card_shows_a_shortened_name(signed_in, user):
    user.first_name = "Sebastian"
    user.last_name = "Schmitz"
    user.save(update_fields=["first_name", "last_name"])

    assert "Sebastian S." in sidebar(signed_in)


def test_the_card_does_not_print_the_address_twice(signed_in, user):
    """The regression itself. Once, in the title, is the whole allowance."""
    assert sidebar(signed_in).count(user.email) == 1


def test_the_address_is_still_reachable_on_hover(signed_in, user):
    assert f'title="{user.email}"' in sidebar(signed_in)


def test_the_card_does_not_list_the_manager_profiles(signed_in, user):
    """The nicknames belong to a roster, not to the person signed in.

    Two profiles here, because one would pass this test by accident.
    """
    Manager.objects.create(user=user, nick_name="Buzz")
    Manager.objects.create(user=user, nick_name="Basti")

    body = sidebar(signed_in)

    assert "Buzz" not in body
    assert "Basti" not in body


def test_the_card_says_one_thing(signed_in, user):
    """The name and the avatar, and no second line under either."""
    user.first_name = "Sebastian"
    user.last_name = "Schmitz"
    user.save(update_fields=["first_name", "last_name"])

    # Sliced at the profile link rather than at the first `title=`, which any
    # nav link could later claim.
    link = f'href="{reverse("accounts:profile")}"'
    card = sidebar(signed_in).split(link)[1].split("</a>")[0]

    assert "Sebastian S." in card
    assert card.count("<span") == 2


def test_seasons_is_hidden_from_regular_users(signed_in):
    assert "Seasons" not in sidebar(signed_in)


def test_seasons_is_hidden_from_staff_who_are_not_superusers(client, staff_user, password):
    client.login(email=staff_user.email, password=password)

    assert "Seasons" not in sidebar(client)


def test_seasons_is_visible_to_staff_superusers(client, staff_user, password):
    staff_user.is_superuser = True
    staff_user.save(update_fields=["is_superuser"])
    client.login(email=staff_user.email, password=password)

    assert f'href="{reverse("fantasy:season-list")}"' in sidebar(client)
