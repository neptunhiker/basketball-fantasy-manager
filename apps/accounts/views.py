from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth import views as auth_views
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.utils.translation import gettext_lazy as _
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.debug import sensitive_post_parameters
from django.views.generic import CreateView, FormView, ListView, TemplateView, View

from .forms import (
    AcceptInvitationForm,
    EmailAuthenticationForm,
    InvitationForm,
    StyledPasswordResetForm,
    StyledSetPasswordForm,
)
from .services import invite_user
from .tokens import invitation_token_generator

User = get_user_model()


class LoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    form_class = EmailAuthenticationForm
    redirect_authenticated_user = True


class LogoutView(auth_views.LogoutView):
    pass


class PasswordResetView(auth_views.PasswordResetView):
    template_name = "accounts/password_reset.html"
    form_class = StyledPasswordResetForm
    email_template_name = "accounts/email/password_reset_body.txt"
    subject_template_name = "accounts/email/password_reset_subject.txt"
    success_url = reverse_lazy("accounts:password-reset-done")
    # Without the sites framework Django would fall back to the bare hostname
    # here, so the mail would sign off as "127.0.0.1:8000".
    extra_email_context = {"site_name": settings.SITE_NAME}


class PasswordResetDoneView(auth_views.PasswordResetDoneView):
    template_name = "accounts/password_reset_done.html"


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"
    form_class = StyledSetPasswordForm
    success_url = reverse_lazy("accounts:password-reset-complete")


class PasswordResetCompleteView(auth_views.PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"


class PasswordChangeView(LoginRequiredMixin, auth_views.PasswordChangeView):
    template_name = "accounts/password_change.html"
    success_url = reverse_lazy("accounts:profile")

    def form_valid(self, form):
        messages.success(self.request, _("Dein Passwort wurde geändert."))
        return super().form_valid(form)


class StaffRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Only team members manage other people's accounts."""

    def test_func(self):
        return self.request.user.is_staff


class ProfileView(LoginRequiredMixin, TemplateView):
    template_name = "accounts/profile.html"


class UserListView(StaffRequiredMixin, ListView):
    model = User
    template_name = "accounts/user_list.html"
    context_object_name = "users"
    paginate_by = 25

    def get_queryset(self):
        qs = super().get_queryset()
        if query := self.request.GET.get("q", "").strip():
            qs = qs.filter(email__icontains=query)
        return qs

    def get_template_names(self):
        # HTMX search requests only need the table body swapped in.
        if self.request.htmx:
            return ["accounts/partials/user_table.html"]
        return super().get_template_names()


class InviteUserView(StaffRequiredMixin, CreateView):
    form_class = InvitationForm
    template_name = "accounts/invite_user.html"
    success_url = reverse_lazy("accounts:user-list")

    def form_valid(self, form):
        response = super().form_valid(form)
        invite_user(self.object, self.request, invited_by=self.request.user)
        messages.success(
            self.request,
            _("Einladung an %(email)s wurde verschickt.") % {"email": self.object.email},
        )
        return response


class ResendInvitationView(StaffRequiredMixin, View):
    """POST-only: sends a fresh invitation link to someone who hasn't accepted."""

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        user = get_object_or_404(User, pk=kwargs["pk"])
        if user.has_accepted_invitation:
            messages.info(request, _("Diese Person hat die Einladung bereits angenommen."))
        else:
            invite_user(user, request, invited_by=request.user)
            messages.success(
                request,
                _("Einladung an %(email)s wurde erneut verschickt.") % {"email": user.email},
            )
        return redirect("accounts:user-list")


@method_decorator([sensitive_post_parameters(), never_cache, csrf_protect], name="dispatch")
class AcceptInvitationView(FormView):
    """
    Two-step, mirroring Django's password-reset confirm view: the token in the
    URL is swapped for a session key, so the token never reaches the form POST
    or a Referer header.
    """

    form_class = AcceptInvitationForm
    template_name = "accounts/accept_invitation.html"
    success_url = reverse_lazy("core:dashboard")
    session_token_key = "_accept_invitation_token"
    internal_url_token = "set-password"

    def dispatch(self, request, *args, **kwargs):
        self.user = self.get_user(kwargs["uidb64"])
        if self.user is None:
            return self.render_invalid()

        token = kwargs["token"]
        if token == self.internal_url_token:
            session_token = request.session.get(self.session_token_key, "")
            if invitation_token_generator.check_token(self.user, session_token):
                self.validlink = True
                return super().dispatch(request, *args, **kwargs)
        elif invitation_token_generator.check_token(self.user, token):
            request.session[self.session_token_key] = token
            redirect_url = request.path.replace(token, self.internal_url_token)
            return redirect(redirect_url)

        return self.render_invalid()

    def get_user(self, uidb64):
        try:
            uid = force_str(urlsafe_base64_decode(uidb64))
            return User.objects.get(pk=uid, is_active=True)
        except (TypeError, ValueError, OverflowError, User.DoesNotExist, ValidationError):
            return None

    def render_invalid(self):
        self.validlink = False
        return self.response_class(
            request=self.request,
            template="accounts/accept_invitation_invalid.html",
            context={"validlink": False},
            using=self.template_engine,
        )

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "user": self.user}

    @transaction.atomic
    def form_valid(self, form):
        user = form.save()
        self.request.session.pop(self.session_token_key, None)
        login(self.request, user, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(self.request, _("Willkommen! Dein Konto ist jetzt aktiv."))
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), "validlink": True, "invited_user": self.user}
