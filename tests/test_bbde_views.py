"""The staff import modal, the players-list button and the management command."""

import datetime as dt
from unittest import mock

import pytest
from django.core.management import CommandError, call_command
from django.urls import reverse

from apps.fantasy.models import Season
from apps.nba.bbde import BbdeAccountNotActivated, BbdeLoginError, BbdeSessionExpired
from apps.nba.models import ImportRun

IMPORT = "apps.nba.views.import_bbde"


@pytest.fixture
def season(db):
    return Season.objects.create(
        label="2026-27",
        starts_on=dt.date(2026, 10, 20),
        ends_on=dt.date(2027, 4, 11),
        is_current=True,
    )


@pytest.fixture
def staff_client(client, staff_user, password):
    client.login(email=staff_user.email, password=password)
    return client


def finished_run(**fields):
    return ImportRun.objects.create(
        source=ImportRun.Source.WEB,
        status=ImportRun.Status.SUCCEEDED,
        pages=31,
        rows=762,
        matched=760,
        created=2,
        **fields,
    )


# --- who may import -------------------------------------------------------------


def test_the_import_needs_a_login(client, db):
    response = client.get(reverse("nba:bbde-import"))

    assert response.status_code == 302


def test_the_import_is_staff_only(client, user, password):
    client.login(email=user.email, password=password)

    assert client.get(reverse("nba:bbde-import")).status_code == 403
    with mock.patch(IMPORT) as import_bbde:
        response = client.post(reverse("nba:bbde-import"), {"username": "a", "password": "b"})
    assert response.status_code == 403
    import_bbde.assert_not_called()


def test_the_button_is_shown_to_staff_only(client, user, staff_user, password):
    client.login(email=user.email, password=password)
    assert "Import from basketball.de" not in client.get(reverse("nba:player-list")).text

    client.login(email=staff_user.email, password=password)
    page = client.get(reverse("nba:player-list")).text
    assert "Import from basketball.de" in page
    assert reverse("nba:bbde-import") in page


def test_the_list_says_when_the_last_import_ran(staff_client, staff_user):
    finished_run(triggered_by=staff_user, finished_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC))
    finished_run(dry_run=True)  # a dry run changed nothing, so it does not count

    page = staff_client.get(reverse("nba:player-list")).text

    assert "Last import from basketball.de:" in page
    assert "by Mia" in page


# --- the modal ------------------------------------------------------------------


def test_the_modal_asks_for_the_login(staff_client):
    response = staff_client.get(reverse("nba:bbde-import"))

    assert response.status_code == 200
    assert 'name="username"' in response.text
    assert 'type="password"' in response.text
    assert "never stored" in response.text


def test_a_successful_import_shows_the_summary(staff_client, staff_user, season):
    run = finished_run(ambiguous=["Chris Johnson (Denver Nuggets): chris-johnson"])

    with mock.patch(IMPORT, return_value=run) as import_bbde:
        response = staff_client.post(
            reverse("nba:bbde-import"), {"username": "coach", "password": "s3cret"}
        )

    import_bbde.assert_called_once_with(
        "coach", "s3cret", triggered_by=staff_user, source=ImportRun.Source.WEB
    )
    assert "Read 762 players from 31 pages." in response.text
    assert "Chris Johnson (Denver Nuggets)" in response.text


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (BbdeLoginError("x"), "did not accept this username and password"),
        (BbdeAccountNotActivated("x"), "has not been activated yet"),
        (BbdeSessionExpired("login page"), "The import failed: login page"),
    ],
)
def test_a_failed_import_keeps_the_form_but_not_the_password(staff_client, error, message):
    with mock.patch(IMPORT, side_effect=error):
        response = staff_client.post(
            reverse("nba:bbde-import"), {"username": "coach", "password": "s3cret"}
        )

    assert response.status_code == 200
    assert message in response.text
    assert 'value="coach"' in response.text
    assert "s3cret" not in response.text


def test_the_real_import_failing_without_a_season_is_explained(staff_client, db):
    response = staff_client.post(
        reverse("nba:bbde-import"), {"username": "coach", "password": "s3cret"}
    )

    assert "There is no current season to import into." in response.text


def test_both_fields_are_required(staff_client):
    with mock.patch(IMPORT) as import_bbde:
        response = staff_client.post(reverse("nba:bbde-import"), {"username": "coach"})

    import_bbde.assert_not_called()
    assert response.status_code == 200
    assert "This field is required." in response.text


# --- the command ----------------------------------------------------------------


def test_the_command_asks_for_the_password_and_reports(db, capsys):
    run = finished_run(warnings=["Something to look at"])

    with (
        mock.patch("apps.nba.management.commands.import_bbde.getpass", return_value="s3cret"),
        mock.patch(
            "apps.nba.management.commands.import_bbde.import_bbde", return_value=run
        ) as import_bbde,
    ):
        call_command("import_bbde", "--username", "coach", "--dry-run")

    import_bbde.assert_called_once_with(
        "coach", "s3cret", source=ImportRun.Source.COMMAND, dry_run=True
    )
    output = capsys.readouterr().out
    assert "762 players; 760 matched, 2 created" in output
    assert "Something to look at" in output


def test_the_command_turns_errors_into_a_command_error(db):
    with (
        mock.patch("apps.nba.management.commands.import_bbde.getpass", return_value="s3cret"),
        mock.patch(
            "apps.nba.management.commands.import_bbde.import_bbde",
            side_effect=BbdeLoginError("basketball.de did not accept this username and password."),
        ),
        pytest.raises(CommandError, match="did not accept"),
    ):
        call_command("import_bbde", "--username", "coach")


def test_the_modal_speaks_german_to_german_staff(staff_client, staff_user):
    staff_user.language = "de"
    staff_user.save()

    response = staff_client.get(reverse("nba:bbde-import"))

    assert "Von basketball.de importieren" in response.text
    assert "nie gespeichert" in response.text
