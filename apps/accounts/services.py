"""Invitation side effects, kept out of the views."""

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django_q.tasks import async_task

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


def send_invitation_email(user_id, invitation_url, invited_by_name=""):
    """Render and deliver one invitation. Runs on the Django-Q worker."""
    user = User.objects.get(pk=user_id)
    context = {
        "user": user,
        "invitation_url": invitation_url,
        "invited_by_name": invited_by_name,
        "site_name": settings.SITE_NAME,
        "expiry_days": settings.INVITATION_TIMEOUT_DAYS,
    }
    subject = render_to_string("accounts/email/invitation_subject.txt", context).strip()
    body = render_to_string("accounts/email/invitation_body.txt", context)
    user.email_user(subject, body, settings.DEFAULT_FROM_EMAIL)
    logger.info("Invitation sent to %s", user.email)


def invite_user(user, request, invited_by=None):
    """Queue an invitation email and record that we sent one."""
    invitation_url = request.build_absolute_uri(build_invitation_path(user))
    invited_by_name = invited_by.display_name if invited_by else ""

    user.last_invited_at = timezone.now()
    user.save(update_fields=["last_invited_at"])

    async_task(
        "apps.accounts.services.send_invitation_email",
        user.pk,
        invitation_url,
        invited_by_name,
        task_name=f"invite-{user.pk}",
    )
    return invitation_url
