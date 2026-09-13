"""Invitation link generation, kept out of the views."""

import logging

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from .tokens import invitation_token_generator

logger = logging.getLogger(__name__)
User = get_user_model()


def build_invitation_path(user):
    """The relative accept-invitation URL for a user, token included."""
    return reverse(
        "accounts:accept-invitation",
        kwargs={
            "uidb64": urlsafe_base64_encode(force_bytes(user.pk)),
            "token": invitation_token_generator.make_token(user),
        },
    )


def build_invitation_url(user, request):
    """The absolute accept-invitation URL for a user."""
    return request.build_absolute_uri(build_invitation_path(user))


def invite_user(user, request, invited_by=None):
    """Record that an invitation link was created and return the URL."""
    invitation_url = build_invitation_url(user, request)

    user.last_invited_at = timezone.now()
    user.save(update_fields=["last_invited_at"])
    return invitation_url
