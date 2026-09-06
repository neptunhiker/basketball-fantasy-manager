import uuid

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError

User = get_user_model()
pytestmark = pytest.mark.django_db


def test_email_is_the_username_field():
    assert User.USERNAME_FIELD == "email"
    assert User.REQUIRED_FIELDS == []
    assert not hasattr(User, "username")


def test_primary_key_is_a_uuid(user):
    assert isinstance(user.pk, uuid.UUID)


def test_create_user_normalises_the_domain():
    user = User.objects.create_user(email="Person@EXAMPLE.COM", password="pw-123456789")
    # normalize_email lowercases the domain but preserves the local part.
    assert user.email == "Person@example.com"


def test_create_user_requires_an_email():
    with pytest.raises(ValueError, match="email address"):
        User.objects.create_user(email="", password="pw-123456789")


def test_email_is_unique(user):
    with pytest.raises(IntegrityError):
        User.objects.create_user(email=user.email, password="pw-123456789")


def test_create_user_without_password_gets_an_unusable_one(invited_user):
    assert not invited_user.has_usable_password()
    assert invited_user.has_accepted_invitation is False


def test_create_superuser():
    admin = User.objects.create_superuser(email="admin@example.com", password="pw-123456789")
    assert admin.is_staff and admin.is_superuser and admin.is_active


@pytest.mark.parametrize(
    "extra",
    [{"is_staff": False}, {"is_superuser": False}],
)
def test_create_superuser_rejects_contradictory_flags(extra):
    with pytest.raises(ValueError):
        User.objects.create_superuser(email="a@example.com", password="pw-123456789", **extra)


def test_display_name_falls_back_to_email(user, staff_user):
    assert user.display_name == "coach@example.com"
    staff_user.last_name = "Berger"
    assert staff_user.display_name == "Mia Berger"


def test_initials(user, staff_user):
    assert user.initials == "C"
    staff_user.last_name = "Berger"
    assert staff_user.initials == "MB"
