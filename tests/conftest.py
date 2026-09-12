import pytest
from django.contrib.auth import get_user_model

from apps.fantasy.models import Manager

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


@pytest.fixture
def manager(user):
    """The profile a roster hangs off.

    One per user here, because most tests only need somewhere for a roster to
    live. A test about several profiles builds the second one itself.
    """
    return Manager.objects.create(user=user, nick_name="Bulla")


@pytest.fixture
def other_manager(db):
    """Somebody else entirely, for the tests that check you cannot see their work.

    A whole second account rather than a second profile on the same one: the
    scoping rule is about the account, so a second profile of your own would
    prove the opposite of what these tests are after.
    """
    other = User.objects.create_user(email="other@example.com", password="x" * 12)
    return Manager.objects.create(user=other, nick_name="Rival")
