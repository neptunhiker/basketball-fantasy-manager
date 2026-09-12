from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("profile/", views.ProfileView.as_view(), name="profile"),
    path("password/change/", views.PasswordChangeView.as_view(), name="password-change"),
    path("password/reset/", views.PasswordResetView.as_view(), name="password-reset"),
    path(
        "password/reset/sent/",
        views.PasswordResetDoneView.as_view(),
        name="password-reset-done",
    ),
    path(
        "password/reset/<uidb64>/<token>/",
        views.PasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path(
        "password/reset/done/",
        views.PasswordResetCompleteView.as_view(),
        name="password-reset-complete",
    ),
    path("team/", views.UserListView.as_view(), name="user-list"),
    path("team/invite/", views.InviteUserView.as_view(), name="invite-user"),
    path(
        "team/<uuid:pk>/resend-invitation/",
        views.ResendInvitationView.as_view(),
        name="resend-invitation",
    ),
    path(
        "invitation/<uidb64>/<token>/",
        views.AcceptInvitationView.as_view(),
        name="accept-invitation",
    ),
]
