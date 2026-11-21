"""The admin side of move corrections: the log is read-only, the balances locked.

A roster's cash and trade count are what its ledger adds up to. Typing either
into the admin used to be the only way to "fix" a mistaken move, and it left
the history page reporting a ledger that no longer reconciles. Corrections now
go through `services.move_transaction` / `undo_transaction`, so the admin may
only look.
"""

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from apps.fantasy.models import Roster, Season, Transaction, TransactionCorrection

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def admin_client(client, db):
    admin = User.objects.create_superuser(email="boss@example.com", password="x" * 12)
    client.force_login(admin)
    return client


@pytest.fixture
def roster(manager):
    season = Season.objects.create(
        label="2026-27",
        starts_on=dt.date(2026, 10, 20),
        ends_on=dt.date(2027, 4, 11),
        starting_cash=Decimal("60000000"),
    )
    return Roster.objects.create(manager=manager, season=season, name="Koalas")


def correction(roster, **overrides):
    values = {
        "roster": roster,
        "transaction_id": uuid.uuid4(),
        "action": TransactionCorrection.Action.MOVED,
        "kind": Transaction.Kind.BUY_TRADE,
        "occurred_at_before": timezone.now() - dt.timedelta(days=2),
        "occurred_at_after": timezone.now() - dt.timedelta(days=1),
        "cash_delta_before": Decimal("-1500000"),
        "cash_delta_after": Decimal("-1500000"),
    }
    values.update(overrides)
    return TransactionCorrection.objects.create(**values)


def test_the_roster_balances_cannot_be_typed_into_the_admin(admin_client, roster):
    url = reverse("admin:fantasy_roster_change", args=[roster.pk])
    page = admin_client.get(url)

    assert page.status_code == 200
    form = page.context["adminform"].form
    for field in ("starting_cash", "cash", "trades_available"):
        assert field not in form.fields


def test_a_posted_balance_is_ignored(admin_client, roster):
    url = reverse("admin:fantasy_roster_change", args=[roster.pk])
    response = admin_client.post(
        url,
        {
            "manager": roster.manager_id,
            "season": roster.season_id,
            "name": "Koalas",
            "icon": "koala",
            "is_active": "on",
            "cash": "99999999",
            "trades_available": "40",
            "memberships-TOTAL_FORMS": "0",
            "memberships-INITIAL_FORMS": "0",
        },
    )

    assert response.status_code == 302
    roster.refresh_from_db()
    assert roster.cash == Decimal("60000000")
    assert roster.trades_available == 0


def test_a_roster_added_in_the_admin_still_takes_its_season_budget(admin_client, roster):
    """With the budget fields gone from the form, `Roster.save` has to fill them."""
    response = admin_client.post(
        reverse("admin:fantasy_roster_add"),
        {
            "manager": roster.manager_id,
            "season": roster.season_id,
            "name": "Second",
            "icon": "koala",
            "memberships-TOTAL_FORMS": "0",
            "memberships-INITIAL_FORMS": "0",
        },
    )

    assert response.status_code == 302
    added = Roster.objects.get(name="Second")
    assert added.starting_cash == added.cash == Decimal("60000000")


def test_the_correction_log_is_listed_but_read_only(admin_client, roster):
    entry = correction(roster)

    listing = admin_client.get(reverse("admin:fantasy_transactioncorrection_changelist"))
    assert listing.status_code == 200
    assert listing.context["cl"].result_count == 1

    detail = admin_client.get(
        reverse("admin:fantasy_transactioncorrection_change", args=[entry.pk])
    )
    assert detail.status_code == 200
    assert not detail.context["has_change_permission"]
    assert admin_client.get(reverse("admin:fantasy_transactioncorrection_add")).status_code == 403
    assert (
        admin_client.get(
            reverse("admin:fantasy_transactioncorrection_delete", args=[entry.pk])
        ).status_code
        == 403
    )


def test_a_move_correction_needs_its_new_date(roster):
    with pytest.raises(IntegrityError), transaction.atomic():
        correction(roster, occurred_at_after=None)


def test_an_undo_has_no_new_date(roster):
    with pytest.raises(IntegrityError), transaction.atomic():
        correction(roster, action=TransactionCorrection.Action.UNDONE, cash_delta_after=None)


def test_an_undo_is_recorded_without_one(roster):
    entry = correction(
        roster,
        action=TransactionCorrection.Action.UNDONE,
        occurred_at_after=None,
        cash_delta_after=None,
    )
    assert str(entry) == "Undone: Buy Trade (Koalas)"
