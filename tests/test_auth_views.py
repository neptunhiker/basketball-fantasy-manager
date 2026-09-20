import pytest
import datetime as dt
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from apps.fantasy.models import Roster, Season

pytestmark = pytest.mark.django_db


def test_login_with_email(client, user, password):
    response = client.post(
        reverse("accounts:login"), {"username": user.email, "password": password}, follow=True
    )
    assert response.status_code == 200
    assert response.wsgi_request.user.is_authenticated


def test_login_is_case_insensitive_on_the_domain(client, user, password):
    response = client.post(
        reverse("accounts:login"),
        {"username": "coach@EXAMPLE.COM", "password": password},
        follow=True,
    )
    assert response.wsgi_request.user.is_authenticated


def test_login_with_a_wrong_password_fails(client, user):
    response = client.post(reverse("accounts:login"), {"username": user.email, "password": "nope"})
    assert response.status_code == 200
    assert not response.wsgi_request.user.is_authenticated
    assert "is not correct" in response.content.decode()


def test_inactive_user_cannot_log_in(client, user, password):
    user.is_active = False
    user.save()
    response = client.post(
        reverse("accounts:login"), {"username": user.email, "password": password}
    )
    assert not response.wsgi_request.user.is_authenticated


def test_dashboard_requires_login(client):
    response = client.get(reverse("core:dashboard"))
    assert response.status_code == 302
    assert reverse("accounts:login") in response["Location"]


def test_dashboard_shows_days_until_current_season(user, password, client, db):
    client.login(username=user.email, password=password)
    season = Season.objects.create(
        label="2026-27",
        starts_on=dt.date.today() + dt.timedelta(days=10),
        ends_on=dt.date.today() + dt.timedelta(days=200),
        is_current=True,
        signings_open_at=timezone.now() - dt.timedelta(days=1),
        signings_close_at=timezone.now() + dt.timedelta(days=5),
    )

    response = client.get(reverse("core:dashboard"))

    assert response.context["current_season"] == season
    assert response.context["days_to_season_start"] == 10
    assert response.context["countdown_label"] == "Signings close in"
    assert response.context["countdown_heading"] == "Complete your roster before signings close."
    assert "Complete your roster before signings close." in response.content.decode()
    assert 'id="trading-countdown"' in response.content.decode()
    assert "The foundation is in place" not in response.content.decode()


def test_dashboard_uses_the_saved_language_for_countdown_copy(user, password, client, db):
    user.language = "de"
    user.save(update_fields=["language"])
    client.login(username=user.email, password=password)
    Season.objects.create(
        label="2026-27",
        starts_on=dt.date.today() + dt.timedelta(days=10),
        ends_on=dt.date.today() + dt.timedelta(days=200),
        is_current=True,
        signings_open_at=timezone.now() - dt.timedelta(days=1),
        signings_close_at=timezone.now() + dt.timedelta(days=5),
    )

    body = client.get(reverse("core:dashboard")).content.decode()

    assert "Transferphase endet in" in body
    assert "Vervollständige deinen Kader" in body
    assert "Tage" in body
    assert "Kader aufstellen" in body


def test_dashboard_has_a_started_season_state(user, password, client, db):
    client.login(username=user.email, password=password)
    Season.objects.create(
        label="2025-26",
        starts_on=dt.date.today() - dt.timedelta(days=10),
        ends_on=dt.date.today() + dt.timedelta(days=100),
        is_current=True,
    )

    response = client.get(reverse("core:dashboard"))

    assert response.context["days_to_season_start"] is None
    assert response.context["countdown_label"] == "Season ends in"
    assert 'id="trading-countdown"' in response.content.decode()


def test_dashboard_shows_the_users_current_season_roster(user, password, client, manager):
    client.login(username=user.email, password=password)
    season = Season.objects.create(
        label="2026-27",
        starts_on=dt.date.today() + dt.timedelta(days=10),
        ends_on=dt.date.today() + dt.timedelta(days=200),
        is_current=True,
    )
    Roster.objects.create(manager=manager, season=season, name="Bulla Ballers")

    response = client.get(reverse("core:dashboard"))

    assert response.context["dashboard_roster"].name == "Bulla Ballers"
    assert "My roster" in response.content.decode()


@pytest.mark.parametrize(
    ("starts_offset", "ends_offset", "open_offset", "close_offset", "expected"),
    [
        (30, 130, 5, 20, "Signings open in"),
        (30, 130, -5, 5, "Signings close in"),
        (5, 105, -20, -5, "Season starts in"),
        (-20, 80, -30, 5, "Signings close in"),
        (-20, 80, -30, -5, "Season ends in"),
        (-40, -1, -50, -45, ""),
    ],
)
def test_dashboard_chooses_the_next_season_deadline(
    user, password, client, starts_offset, ends_offset, open_offset, close_offset, expected
):
    client.login(username=user.email, password=password)
    today = timezone.localdate()
    Season.objects.create(
        label="2026-27",
        starts_on=today + dt.timedelta(days=starts_offset),
        ends_on=today + dt.timedelta(days=ends_offset),
        is_current=True,
        signings_open_at=timezone.now() + dt.timedelta(days=open_offset),
        signings_close_at=timezone.now() + dt.timedelta(days=close_offset),
    )

    response = client.get(reverse("core:dashboard"))

    assert response.context["countdown_label"] == expected
    assert ("id=\"trading-countdown\"" in response.content.decode()) is bool(expected)


def test_logout_requires_post(client, user, password):
    client.login(username=user.email, password=password)
    assert client.get(reverse("accounts:logout")).status_code == 405
    assert client.post(reverse("accounts:logout")).status_code == 302


def test_password_reset_sends_an_email(client, user):
    response = client.post(reverse("accounts:password-reset"), {"email": user.email})
    assert response.status_code == 302
    assert len(mail.outbox) == 1
    assert reverse("accounts:password-reset").split("reset")[0] in mail.outbox[0].body


def test_password_reset_follows_the_browser_language(client):
    response = client.get(
        reverse("accounts:password-reset"), headers={"accept-language": "de"}
    )

    body = response.content.decode()
    assert "Passwort zurücksetzen" in body
    assert "Link senden" in body


def test_password_reset_for_an_unknown_address_reveals_nothing(client, db):
    response = client.post(reverse("accounts:password-reset"), {"email": "nobody@example.com"})
    assert response.status_code == 302
    assert len(mail.outbox) == 0


def test_user_list_is_staff_only(client, user, staff_user, password):
    client.login(username=user.email, password=password)
    assert client.get(reverse("accounts:user-list")).status_code == 403

    client.logout()
    client.login(username=staff_user.email, password=password)
    assert client.get(reverse("accounts:user-list")).status_code == 200


def test_user_list_uses_the_staff_members_saved_language(client, staff_user, password):
    staff_user.language = "de"
    staff_user.save(update_fields=["language"])
    client.login(username=staff_user.email, password=password)

    body = client.get(reverse("accounts:user-list")).content.decode()

    assert "Einladen" in body
    assert "Person" in body
    assert "Status" in body


def test_user_list_search_returns_only_the_table_for_htmx(client, staff_user, user, password):
    client.login(username=staff_user.email, password=password)
    response = client.get(
        reverse("accounts:user-list"), {"q": "coach"}, headers={"hx-request": "true"}
    )
    body = response.content.decode()
    assert response.status_code == 200
    assert "<html" not in body
    assert user.email in body
    assert staff_user.email not in body


def test_user_detail_is_staff_only(client, user, staff_user, password, manager):
    url = reverse("accounts:user-detail", args=[user.pk])

    client.login(username=user.email, password=password)
    assert client.get(url).status_code == 403

    client.logout()
    client.login(username=staff_user.email, password=password)
    response = client.get(url)
    assert response.status_code == 200
    assert manager.nick_name in response.content.decode()


def test_user_detail_links_to_its_manager_profiles(client, staff_user, user, password, manager):
    client.login(username=staff_user.email, password=password)
    body = client.get(reverse("accounts:user-detail", args=[user.pk])).content.decode()

    assert reverse("fantasy:manager-detail", args=[manager.pk]) in body


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_password_reset_email_signs_off_with_the_product_name(client, user, settings):
    """Django renders this mail without a request, so the context processor that
    normally supplies SITE_NAME never runs. Without extra_email_context it would
    fall back to the bare hostname and sign off as "testserver"."""
    client.post(reverse("accounts:password-reset"), {"email": user.email})
    body = mail.outbox[0].body
    assert body.rstrip().endswith(settings.SITE_NAME)
    assert "testserver" not in body.rstrip().splitlines()[-1]
