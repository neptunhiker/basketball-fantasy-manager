"""Editing your own account: the name, and the address you sign in with.

The address is the interesting half. It is the credential, not a detail, so
these tests care about three things beyond "the field saves": that a clash is
refused, that the account cannot be left without an address, and that after a
change the new address is the one that gets you back in.
"""

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

URL = reverse("accounts:profile")


@pytest.fixture
def signed_in(client, user, password):
    client.login(email=user.email, password=password)
    return client


def form_values(**overrides):
    """A complete POST. Omitting a field is a different test, so spell it out."""
    return {"first_name": "Sebastian", "last_name": "Schmitz", "email": "coach@example.com"} | (
        overrides
    )


# --- what the page shows -----------------------------------------------------


def test_the_page_needs_a_login(client):
    response = client.get(URL)

    assert response.status_code == 302
    assert reverse("accounts:login") in response["Location"]


def test_the_names_are_shown_in_the_form(signed_in, user):
    user.first_name = "Sebastian"
    user.last_name = "Schmitz"
    user.save(update_fields=["first_name", "last_name"])

    body = signed_in.get(URL).content.decode()

    assert 'value="Sebastian"' in body
    assert 'value="Schmitz"' in body
    assert f'value="{user.email}"' in body


def test_the_full_name_heads_the_card(signed_in, user):
    user.first_name = "Sebastian"
    user.last_name = "Schmitz"
    user.save(update_fields=["first_name", "last_name"])

    assert "Sebastian Schmitz" in signed_in.get(URL).content.decode()


def test_an_account_with_no_name_is_told_so(signed_in):
    """Rather than a blank line where the name goes. It is also the answer to
    why the sidebar shows an address instead of a name."""
    assert "No name yet" in signed_in.get(URL).content.decode()


def test_the_labels_are_english(signed_in):
    """The model's `verbose_name`s were German, and this form is the first
    screen to render them."""
    body = signed_in.get(URL).content.decode()

    assert "First name" in body
    assert "Vorname" not in body
    assert "E-Mail-Adresse" not in body


# --- saving ------------------------------------------------------------------


def test_the_name_can_be_changed(signed_in, user):
    response = signed_in.post(URL, form_values())

    assert response.status_code == 302
    user.refresh_from_db()
    assert (user.first_name, user.last_name) == ("Sebastian", "Schmitz")


def test_the_name_can_be_cleared(signed_in, user):
    """Both name fields are optional on the model, so emptying them is allowed."""
    user.first_name = "Sebastian"
    user.save(update_fields=["first_name"])

    signed_in.post(URL, form_values(first_name="", last_name=""))

    user.refresh_from_db()
    assert user.full_name == ""


def test_saving_says_so(signed_in):
    response = signed_in.post(URL, form_values(), follow=True)

    assert "Your details have been saved." in response.content.decode()


def test_the_new_name_reaches_the_sidebar(signed_in, user):
    """The abbreviated name is built from these two fields, so this is the
    round trip the whole change is for."""
    signed_in.post(URL, form_values())

    body = signed_in.get(reverse("fantasy:roster-list")).content.decode()

    assert "Sebastian S." in body


# --- the address -------------------------------------------------------------


def test_the_address_can_be_changed(signed_in, user):
    signed_in.post(URL, form_values(email="new@example.com"))

    user.refresh_from_db()
    assert user.email == "new@example.com"


def test_the_new_address_is_the_one_that_signs_you_in(client, signed_in, user, password):
    signed_in.post(URL, form_values(email="new@example.com"))
    signed_in.post(reverse("accounts:logout"))

    assert not client.login(email="coach@example.com", password=password)
    assert client.login(email="new@example.com", password=password)


def test_changing_the_address_does_not_sign_you_out(signed_in):
    """Django's session hash comes from the password, which this form leaves
    alone -- so the redirect after saving lands on the page, not on login."""
    response = signed_in.post(URL, form_values(email="new@example.com"), follow=True)

    assert response.wsgi_request.user.is_authenticated
    assert response.wsgi_request.path == URL


def test_the_domain_is_normalised(signed_in, user):
    signed_in.post(URL, form_values(email="Coach@EXAMPLE.COM"))

    user.refresh_from_db()
    assert user.email == "Coach@example.com"


def test_the_address_cannot_be_emptied(signed_in, user):
    response = signed_in.post(URL, form_values(email=""))

    assert response.status_code == 200
    user.refresh_from_db()
    assert user.email == "coach@example.com"


def test_someone_elses_address_is_refused(signed_in, user, other_manager):
    response = signed_in.post(URL, form_values(email=other_manager.user.email))

    assert "already exists" in response.content.decode()
    user.refresh_from_db()
    assert user.email == "coach@example.com"


def test_someone_elses_address_is_refused_in_any_casing(signed_in, user, other_manager):
    """`unique=True` would only catch the exact string, but sign-in lowercases
    the domain first -- so a second row would compete for the same login."""
    response = signed_in.post(URL, form_values(email=other_manager.user.email.upper()))

    assert "already exists" in response.content.decode()
    user.refresh_from_db()
    assert user.email == "coach@example.com"


def test_keeping_your_own_address_is_not_a_clash(signed_in, user):
    """The obvious way to write the uniqueness check refuses this."""
    response = signed_in.post(URL, form_values())

    assert response.status_code == 302
    user.refresh_from_db()
    assert user.first_name == "Sebastian"


def test_only_your_own_account_is_editable(signed_in, other_manager):
    """There is no pk in the URL, so this is what the view's `get_object`
    promises: the POST reaches one account and it is the signed-in one."""
    other = other_manager.user

    signed_in.post(URL, form_values())

    other.refresh_from_db()
    assert other.first_name == ""


def test_the_form_cannot_grant_staff_access(signed_in, user):
    """Not a field on the form, so a hand-written POST carrying it changes
    nothing. Worth a test: it is the one privilege on this model."""
    signed_in.post(URL, form_values() | {"is_staff": "on", "is_superuser": "on"})

    user.refresh_from_db()
    assert not user.is_staff
    assert not user.is_superuser
