from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("profil/", views.ProfileView.as_view(), name="profile"),
    path("passwort/aendern/", views.PasswordChangeView.as_view(), name="password-change"),
    path("passwort/reset/", views.PasswordResetView.as_view(), name="password-reset"),
    path(
        "passwort/reset/gesendet/",
        views.PasswordResetDoneView.as_view(),
        name="password-reset-done",
    ),
    path(
        "passwort/reset/<uidb64>/<token>/",
        views.PasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path(
        "passwort/reset/fertig/",
        views.PasswordResetCompleteView.as_view(),
        name="password-reset-complete",
    ),
    path("team/", views.UserListView.as_view(), name="user-list"),
    path("team/einladen/", views.InviteUserView.as_view(), name="invite-user"),
    path(
        "team/<uuid:pk>/einladung-erneut/",
        views.ResendInvitationView.as_view(),
        name="resend-invitation",
    ),
    path(
        "einladung/<uidb64>/<token>/",
        views.AcceptInvitationView.as_view(),
        name="accept-invitation",
    ),
]
