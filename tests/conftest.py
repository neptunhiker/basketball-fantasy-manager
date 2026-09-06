import pytest
from django.contrib.auth import get_user_model

User = get_user_model()


@pytest.fixture
def password():
    return "correct-horse-battery"


@pytest.fixture
def user(db, password):
    return User.objects.create_user(email="coach@example.com", password=password)


@pytest.fixture
def staff_user(db, password):
    return User.objects.create_user(
        email="team@example.com", password=password, is_staff=True, first_name="Mia"
    )


@pytest.fixture
def invited_user(db):
    """A user who has been invited but has not set a password yet."""
    return User.objects.create_user(email="neu@example.com")
