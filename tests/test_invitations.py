from datetime import timedelta

import pytest
from django.core import mail
from django.urls import reverse

from apps.accounts.services import build_invitation_path
from apps.accounts.tokens import invitation_token_generator

pytestmark = pytest.mark.django_db


def test_staff_can_invite_a_user(client, staff_user, password):
    client.login(username=staff_user.email, password=password)
    response = client.post(
        reverse("accounts:invite-user"),
        {"email": "neu@example.com", "first_name": "Jo", "last_name": "", "is_staff": ""},
        follow=True,
    )
    assert response.status_code == 200

    from django.contrib.auth import get_user_model

    invitee = get_user_model().objects.get(email="neu@example.com")
    assert not invitee.has_usable_password()
    assert invitee.last_invited_at is not None

    assert len(mail.outbox) == 1
    assert invitee.email in mail.outbox[0].to
    assert "einladen" in mail.outbox[0].body.lower() or "eingeladen" in mail.outbox[0].body.lower()


def test_non_staff_cannot_invite(client, user, password):
    client.login(username=user.email, password=password)
    response = client.post(reverse("accounts:invite-user"), {"email": "x@example.com"})
    assert response.status_code == 403


def test_invitation_link_lets_the_user_set_a_password(client, invited_user):
    path = build_invitation_path(invited_user)

    # The token in the URL is exchanged for a session key, then the form renders.
    response = client.get(path)
    assert response.status_code == 302
    response = client.get(response["Location"])
    assert response.status_code == 200

    response = client.post(
        response.request["PATH_INFO"],
        {
            "first_name": "Jo",
            "last_name": "Berger",
            "new_password1": "korrektes-pferd-9",
            "new_password2": "korrektes-pferd-9",
        },
        follow=True,
    )
    assert response.status_code == 200

    invited_user.refresh_from_db()
    assert invited_user.check_password("korrektes-pferd-9")
    assert invited_user.first_name == "Jo"
    assert invited_user.has_accepted_invitation
    # And they are signed in afterwards.
    assert response.wsgi_request.user == invited_user


def test_invitation_token_is_single_use(invited_user):
    token = invitation_token_generator.make_token(invited_user)
    assert invitation_token_generator.check_token(invited_user, token)

    invited_user.set_password("etwas-neues-123")
    invited_user.save()

    assert not invitation_token_generator.check_token(invited_user, token)


def test_invitation_token_expires(settings, monkeypatch, invited_user):
    settings.INVITATION_TIMEOUT_DAYS = 14
    token = invitation_token_generator.make_token(invited_user)
    assert invitation_token_generator.check_token(invited_user, token)

    # Same token, checked 15 days later.
    real_now = invitation_token_generator._now()
    monkeypatch.setattr(
        type(invitation_token_generator),
        "_now",
        lambda self: real_now + timedelta(days=15),
    )
    assert not invitation_token_generator.check_token(invited_user, token)


def test_invitation_token_outlives_the_shorter_password_reset_window(settings, invited_user):
    """The invitation window is independent of PASSWORD_RESET_TIMEOUT."""
    settings.PASSWORD_RESET_TIMEOUT = 1
    settings.INVITATION_TIMEOUT_DAYS = 14
    token = invitation_token_generator.make_token(invited_user)
    assert invitation_token_generator.check_token(invited_user, token)


def test_password_reset_token_is_not_accepted_as_an_invitation(invited_user):
    from django.contrib.auth.tokens import default_token_generator

    reset_token = default_token_generator.make_token(invited_user)
    assert not invitation_token_generator.check_token(invited_user, reset_token)


def test_garbled_invitation_link_shows_the_invalid_page(client, invited_user):
    path = build_invitation_path(invited_user)
    broken = path.replace(path.rstrip("/").rsplit("/", 1)[1], "not-a-real-token")
    response = client.get(broken)
    assert response.status_code == 200
    assert not response.context["validlink"]


def test_resend_invitation(client, staff_user, invited_user, password):
    client.login(username=staff_user.email, password=password)
    response = client.post(
        reverse("accounts:resend-invitation", kwargs={"pk": invited_user.pk}), follow=True
    )
    assert response.status_code == 200
    assert len(mail.outbox) == 1


def test_resend_does_nothing_for_an_active_user(client, staff_user, user, password):
    client.login(username=staff_user.email, password=password)
    client.post(reverse("accounts:resend-invitation", kwargs={"pk": user.pk}), follow=True)
    assert len(mail.outbox) == 0
