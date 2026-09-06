import pytest
from django.core import mail
from django.urls import reverse

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
    assert "nicht korrekt" in response.content.decode()


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


def test_logout_requires_post(client, user, password):
    client.login(username=user.email, password=password)
    assert client.get(reverse("accounts:logout")).status_code == 405
    assert client.post(reverse("accounts:logout")).status_code == 302


def test_password_reset_sends_an_email(client, user):
    response = client.post(reverse("accounts:password-reset"), {"email": user.email})
    assert response.status_code == 302
    assert len(mail.outbox) == 1
    assert reverse("accounts:password-reset").split("reset")[0] in mail.outbox[0].body


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
