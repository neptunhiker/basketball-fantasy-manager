import uuid

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .managers import UserManager


class User(AbstractBaseUser, PermissionsMixin):
    """A staff member, coordinator or coach. Identified by email, never a username."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(_("E-Mail-Adresse"), unique=True)
    first_name = models.CharField(_("Vorname"), max_length=150, blank=True)
    last_name = models.CharField(_("Nachname"), max_length=150, blank=True)

    is_active = models.BooleanField(
        _("aktiv"),
        default=True,
        help_text=_("Deaktivieren statt löschen, um Konten zu sperren."),
    )
    is_staff = models.BooleanField(
        _("Team-Mitglied"),
        default=False,
        help_text=_("Legt fest, ob sich die Person im Admin-Bereich anmelden darf."),
    )

    date_joined = models.DateTimeField(_("beigetreten am"), default=timezone.now)
    last_invited_at = models.DateTimeField(_("zuletzt eingeladen am"), null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = _("Benutzer")
        verbose_name_plural = _("Benutzer")
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
