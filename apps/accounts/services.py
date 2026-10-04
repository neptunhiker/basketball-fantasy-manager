"""Account actions kept out of the views: invitation links and role changes."""

import logging

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.utils.translation import gettext as _

from .models import Role
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


@transaction.atomic
def change_role(actor, person, role):
    """Give `person` the `role`, if `actor` may and the team stays administrable.

    Only superusers hand out roles. Nobody changes their own role, so an
    administrator cannot lock themselves out by accident, and the last active
    administrator cannot be demoted, so the team page always has someone who
    can change roles.
    """
    role = Role(role)
    if not actor.is_superuser:
        raise PermissionDenied
    if actor.pk == person.pk:
        raise ValidationError(_("You cannot change your own role."))
    if person.is_superuser and role != Role.ADMIN:
        # Locked, so two administrators demoting each other at the same moment
        # cannot both succeed and leave nobody in charge.
        admins = list(
            User.objects.select_for_update().filter(is_active=True, is_superuser=True)
        )
        if not any(admin.pk != person.pk for admin in admins):
            raise ValidationError(_("The last administrator cannot lose that role."))

    person.is_staff = role in (Role.TEAM, Role.ADMIN)
    person.is_superuser = role == Role.ADMIN
    person.save(update_fields=["is_staff", "is_superuser"])
    logger.info("Role of %s changed to %s by %s", person.pk, role.value, actor.pk)
    return person
