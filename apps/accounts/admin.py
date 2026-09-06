from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _

from .forms import AdminUserChangeForm, AdminUserCreationForm
from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    form = AdminUserChangeForm
    add_form = AdminUserCreationForm

    list_display = ["email", "first_name", "last_name", "is_staff", "is_active", "date_joined"]
    list_filter = ["is_staff", "is_superuser", "is_active"]
    search_fields = ["email", "first_name", "last_name"]
    ordering = ["email"]
    readonly_fields = ["date_joined", "last_login", "last_invited_at"]

    fieldsets = [
        (None, {"fields": ["email", "password"]}),
        (_("Persönliche Angaben"), {"fields": ["first_name", "last_name"]}),
        (
            _("Berechtigungen"),
            {"fields": ["is_active", "is_staff", "is_superuser", "groups", "user_permissions"]},
        ),
        (_("Zeitstempel"), {"fields": ["last_login", "date_joined", "last_invited_at"]}),
    ]

    add_fieldsets = [
        (
            None,
            {
                "classes": ["wide"],
                "fields": ["email", "first_name", "last_name", "password1", "password2"],
            },
        ),
    ]
