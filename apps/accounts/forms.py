from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordResetForm,
    SetPasswordForm,
    UserChangeForm,
    UserCreationForm,
)
from django.utils.translation import gettext_lazy as _

from apps.core.forms import StyledFormMixin

User = get_user_model()


class EmailAuthenticationForm(StyledFormMixin, AuthenticationForm):
    """Login form; the username field is an email address."""

    username = forms.EmailField(
        label=_("Email address"),
        widget=forms.EmailInput(attrs={"autofocus": True, "autocomplete": "email"}),
    )

    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": _("That email address or password is not correct."),
        "inactive": _("This account is deactivated."),
    }

    def clean_username(self):
        return User.objects.normalize_email(self.cleaned_data["username"])


class InvitationForm(StyledFormMixin, forms.ModelForm):
    """Used by staff to invite a new user. No password is set here."""

    class Meta:
        model = User
        fields = ["email", "first_name", "last_name", "is_staff"]
        labels = {
            "is_staff": _("Access to the admin site"),
        }

    def clean_email(self):
        email = User.objects.normalize_email(self.cleaned_data["email"])
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(_("An account with that address already exists."))
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        # Invited users have no password until they follow the link.
        user.set_unusable_password()
        if commit:
            user.save()
        return user


class ProfileForm(StyledFormMixin, forms.ModelForm):
    """Where a signed-in user edits their own name and login address.

    The address is on the same form as the name, which is worth being explicit
    about: it is not one more detail about the person, it is the credential they
    sign in with. Changing it changes how they get back in -- hence the help
    text, and hence the field being required. An account with no address would
    have no way to authenticate and no way to be sent a reset link.

    No `is_staff` and no `is_active` here. This form is the one place a user
    edits their own record, and neither of those is theirs to grant.
    """

    class Meta:
        model = User
        fields = ["first_name", "last_name", "email"]
        # On this screen only. The invitation form edits somebody else's
        # address, where "you" would be the wrong person.
        help_texts = {"email": _("You sign in with this address.")}

    def clean_email(self):
        """Normalised and checked case-insensitively, as sign-in does.

        `unique=True` on the column already refuses an exact duplicate, but the
        login form lowercases the domain before it looks anyone up -- so
        `coach@EXAMPLE.com` would be stored as a second row and then compete
        with the first for the same sign-in. Excluding `self.instance` is what
        lets someone save the form without changing their address.
        """
        email = User.objects.normalize_email(self.cleaned_data["email"])
        if User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError(_("An account with that address already exists."))
        return email


class AcceptInvitationForm(StyledFormMixin, SetPasswordForm):
    """Where an invited user chooses their password and confirms their name."""

    first_name = forms.CharField(label=_("First name"), max_length=150, required=False)
    last_name = forms.CharField(label=_("Last name"), max_length=150, required=False)

    field_order = ["first_name", "last_name", "new_password1", "new_password2"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["first_name"].initial = self.user.first_name
        self.fields["last_name"].initial = self.user.last_name

    def save(self, commit=True):
        user = super().save(commit=False)
        user.first_name = self.cleaned_data["first_name"]
        user.last_name = self.cleaned_data["last_name"]
        if commit:
            user.save()
        return user


class StyledPasswordResetForm(StyledFormMixin, PasswordResetForm):
    pass


class StyledSetPasswordForm(StyledFormMixin, SetPasswordForm):
    pass


class AdminUserCreationForm(UserCreationForm):
    class Meta:
        model = User
        fields = ["email", "first_name", "last_name"]


class AdminUserChangeForm(UserChangeForm):
    class Meta:
        model = User
        fields = "__all__"
