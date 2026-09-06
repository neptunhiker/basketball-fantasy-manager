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

User = get_user_model()

# Every field in the app gets these classes, so the widgets stay consistent
# without repeating Tailwind strings in each template.
INPUT_CLASSES = (
    "block w-full rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm "
    "text-tertiary-700 placeholder-neutral-400 shadow-sm transition "
    "focus:border-primary-500 focus:outline-none focus:ring-2 focus:ring-primary-500/30 "
    "disabled:bg-neutral-100 dark:border-neutral-700 dark:bg-neutral-900 "
    "dark:text-neutral-100 dark:placeholder-neutral-500"
)


class StyledFormMixin:
    """Applies the shared input styling to every visible widget."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault(
                    "class",
                    "h-4 w-4 rounded border-neutral-300 text-primary-500 focus:ring-primary-500/30",
                )
            else:
                widget.attrs.setdefault("class", INPUT_CLASSES)
            if field.required:
                widget.attrs.setdefault("required", "required")


class EmailAuthenticationForm(StyledFormMixin, AuthenticationForm):
    """Login form; the username field is an email address."""

    username = forms.EmailField(
        label=_("E-Mail-Adresse"),
        widget=forms.EmailInput(attrs={"autofocus": True, "autocomplete": "email"}),
    )

    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": _("E-Mail-Adresse oder Passwort ist nicht korrekt."),
        "inactive": _("Dieses Konto ist deaktiviert."),
    }

    def clean_username(self):
        return User.objects.normalize_email(self.cleaned_data["username"])


class InvitationForm(StyledFormMixin, forms.ModelForm):
    """Used by staff to invite a new user. No password is set here."""

    class Meta:
        model = User
        fields = ["email", "first_name", "last_name", "is_staff"]
        labels = {
            "is_staff": _("Zugriff auf den Admin-Bereich"),
        }

    def clean_email(self):
        email = User.objects.normalize_email(self.cleaned_data["email"])
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(_("Es existiert bereits ein Konto mit dieser Adresse."))
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        # Invited users have no password until they follow the link.
        user.set_unusable_password()
        if commit:
            user.save()
        return user


class AcceptInvitationForm(StyledFormMixin, SetPasswordForm):
    """Where an invited user chooses their password and confirms their name."""

    first_name = forms.CharField(label=_("Vorname"), max_length=150, required=False)
    last_name = forms.CharField(label=_("Nachname"), max_length=150, required=False)

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
