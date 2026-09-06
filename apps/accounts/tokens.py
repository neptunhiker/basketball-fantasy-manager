from django.conf import settings
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils.crypto import constant_time_compare
from django.utils.http import base36_to_int


class InvitationTokenGenerator(PasswordResetTokenGenerator):
    """
    One-time tokens for invitation links.

    Same construction as Django's password-reset token — the hash covers the
    password field, so the link stops working the moment the invitee sets a
    password — but with its own salt and its own, longer expiry window.
    """

    key_salt = "apps.accounts.tokens.InvitationTokenGenerator"

    @property
    def timeout(self):
        return settings.INVITATION_TIMEOUT_DAYS * 24 * 60 * 60

    def check_token(self, user, token):
        # Reimplemented rather than delegated: Django's version hardcodes
        # settings.PASSWORD_RESET_TIMEOUT, and invitations live longer than resets.
        if not (user and token):
            return False
        try:
            ts_b36, _ = token.split("-")
            ts = base36_to_int(ts_b36)
        except ValueError:
            return False

        if (self._num_seconds(self._now()) - ts) > self.timeout:
            return False

        return any(
            constant_time_compare(self._make_token_with_timestamp(user, ts, secret), token)
            for secret in [self.secret, *self.secret_fallbacks]
        )


invitation_token_generator = InvitationTokenGenerator()
