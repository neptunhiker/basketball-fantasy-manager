import uuid

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .managers import UserManager


class User(AbstractBaseUser, PermissionsMixin):
    """A staff member, coordinator or coach. Identified by email, never a username."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(_("Email address"), unique=True)
    first_name = models.CharField(_("First name"), max_length=150, blank=True)
    last_name = models.CharField(_("Last name"), max_length=150, blank=True)

    is_active = models.BooleanField(
        _("Active"),
        default=True,
        help_text=_("Deactivate instead of deleting to lock an account out."),
    )
    is_staff = models.BooleanField(
        _("Team member"),
        default=False,
        help_text=_("Whether this person may sign in to the admin site."),
    )

    date_joined = models.DateTimeField(_("Joined"), default=timezone.now)
    last_invited_at = models.DateTimeField(_("Last invited"), null=True, blank=True)
    language = models.CharField(
        _("Language"),
        max_length=10,
        choices=settings.LANGUAGES,
        default="en",
    )

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = _("User")
        verbose_name_plural = _("Users")
        ordering = ["email"]

    def __str__(self):
        return self.email

    def clean(self):
        super().clean()
        self.email = self.__class__.objects.normalize_email(self.email)

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    def get_full_name(self):
        return self.full_name or self.email

    def get_short_name(self):
        return self.first_name or self.email.split("@")[0]

    @property
    def display_name(self):
        """What to show in the UI: a real name if we have one, else the email."""
        return self.get_full_name()

    @property
    def abbreviated_name(self):
        """A name for somewhere narrow, like the sidebar: `Sebastian S.`

        Shortens the surname rather than the given name, because the given name
        is the half people are addressed by. Falls back through whatever is
        actually known: a surname alone stays whole, since "S." on its own
        identifies nobody, and an account with no name at all shows the local
        part of its address so the card is never blank.
        """
        if self.first_name and self.last_name:
            return f"{self.first_name} {self.last_name[0].upper()}."
        return self.first_name or self.last_name or self.email.split("@")[0]

    @property
    def initials(self):
        parts = [p for p in (self.first_name, self.last_name) if p]
        if parts:
            return "".join(p[0].upper() for p in parts)
        return self.email[0].upper()

    @property
    def has_accepted_invitation(self):
        """True once the user has set a password of their own."""
        return self.has_usable_password()

    def email_user(self, subject, message, from_email=None, **kwargs):
        from django.core.mail import send_mail

        send_mail(subject, message, from_email, [self.email], **kwargs)
