"""The superuser pages for season stats: upload, preview, confirm, discard."""

import datetime as dt

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from apps.nba.models import Player, PlayerSeasonStats, SeasonStatsImport
from apps.nba.season_stats import PREVIEW_LIFETIME, preview_season_stats
from apps.nba.season_stats_views import last_finished_season

from .test_season_stats_parser import LUKA, SGA, line, stats_file

UPLOAD = reverse("nba:season-stats-upload")


@pytest.fixture
def superuser(user):
    user.is_superuser = True
    user.save(update_fields=["is_superuser"])
    return user


@pytest.fixture
def admin_client(client, superuser, password):
    client.login(email=superuser.email, password=password)
    return client


@pytest.fixture
def players(db):
    return {
        "luka": Player.objects.create(first_name="Luka", last_name="Doncic", position="G"),
        "shai": Player.objects.create(
            first_name="Shai", last_name="Gilgeous-Alexander", position="G"
        ),
        "gg": Player.objects.create(first_name="Gregory", last_name="Jackson", position="F"),
    }


def upload(client, *lines, season="2025-26", name="stats.csv"):
    file = SimpleUploadedFile(name, stats_file(*lines), content_type="text/csv")
    return client.post(UPLOAD, {"season": season, "file": file})


def preview_for(user, *lines):
    return preview_season_stats(stats_file(*lines), season="2025-26", triggered_by=user)


def import_url(run, action=""):
    name = f"nba:season-stats-{action}" if action else "nba:season-stats-import"
    return reverse(name, args=[run.pk])


# --- who may come in ------------------------------------------------------------------


def all_urls(run):
    return [
        ("get", UPLOAD),
        ("post", UPLOAD),
        ("get", import_url(run)),
        ("post", import_url(run, "confirm")),
        ("post", import_url(run, "discard")),
    ]


@pytest.fixture
def open_run(players, superuser):
    return preview_for(superuser, LUKA)


def test_anonymous_visitors_are_sent_to_log_in(client, open_run):
    for method, url in all_urls(open_run):
        response = getattr(client, method)(url)
        assert response.status_code == 302
        assert response["Location"].startswith(reverse("accounts:login"))


def test_ordinary_users_are_refused(client, open_run, password, django_user_model):
    django_user_model.objects.create_user(email="fan@example.com", password=password)
    client.login(email="fan@example.com", password=password)

    for method, url in all_urls(open_run):
        assert getattr(client, method)(url).status_code == 403


def test_staff_who_are_not_superusers_are_refused(client, open_run, staff_user, password):
    client.login(email=staff_user.email, password=password)

    for method, url in all_urls(open_run):
        assert getattr(client, method)(url).status_code == 403
    open_run.refresh_from_db()
    assert open_run.status == SeasonStatsImport.Status.PREVIEWED
    assert not PlayerSeasonStats.objects.exists()


def test_the_players_list_offers_the_page_to_superusers_only(
    client, superuser, staff_user, password
):
    client.login(email=staff_user.email, password=password)
    assert UPLOAD not in client.get(reverse("nba:player-list")).content.decode()

    client.login(email=superuser.email, password=password)
    assert UPLOAD in client.get(reverse("nba:player-list")).content.decode()


# --- the upload page ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("today", "season"),
    [
        (dt.date(2026, 10, 13), "2025-26"),
        (dt.date(2026, 7, 1), "2025-26"),
        (dt.date(2026, 6, 30), "2024-25"),
        (dt.date(2027, 1, 5), "2025-26"),
    ],
)
def test_the_form_suggests_the_season_that_ended_last(today, season):
    assert last_finished_season(today) == season


def test_the_upload_page_shows_the_form_and_history(admin_client, open_run):
    response = admin_client.get(UPLOAD)

    assert response.status_code == 200
    page = response.content.decode()
    assert 'enctype="multipart/form-data"' in page
    assert 'accept=".csv,text/csv"' in page
    assert import_url(open_run) in page
    assert "Preview open" in page


def test_the_page_lists_stored_seasons(admin_client, superuser, players):
    admin_client.post(import_url(preview_for(superuser, LUKA, SGA), "confirm"))

    page = admin_client.get(UPLOAD).content.decode()

    assert "2025-26" in page
    assert "2 players" in page


def test_an_upload_opens_its_preview_and_saves_no_stats(admin_client, superuser, players):
    response = upload(admin_client, LUKA, SGA, line("Kyle Lowry", "PHI"))

    run = SeasonStatsImport.objects.get()
    assert response.status_code == 302
    assert response["Location"] == import_url(run)
    assert run.triggered_by == superuser
    assert run.file_name == "stats.csv"
    assert (run.matched, run.rows) == (2, 3)
    assert not PlayerSeasonStats.objects.exists()


def test_a_bad_season_is_shown_on_the_form(admin_client, players):
    response = upload(admin_client, LUKA, season="2025")

    assert response.status_code == 400
    assert "YYYY-YY" in response.content.decode()
    assert not SeasonStatsImport.objects.exists()


def test_an_unusable_file_is_shown_on_the_form(admin_client, players):
    file = SimpleUploadedFile("stats.csv", b"PLAYER;TEAM\r\nX;Y\r\n", content_type="text/csv")

    response = admin_client.post(UPLOAD, {"season": "2025-26", "file": file})

    assert response.status_code == 400
    assert "These columns are missing" in response.content.decode()
    assert not SeasonStatsImport.objects.exists()


def test_a_missing_file_is_shown_on_the_form(admin_client):
    response = admin_client.post(UPLOAD, {"season": "2025-26"})

    assert response.status_code == 400
    assert not SeasonStatsImport.objects.exists()


# --- the preview ----------------------------------------------------------------------


def test_the_preview_shows_counts_sample_and_lines_to_check(admin_client, superuser, players):
    run = preview_for(superuser, LUKA, line("GG Jackson", "MEM"), line("Kyle Lowry", "PHI"))

    response = admin_client.get(import_url(run))

    page = response.content.decode()
    assert response.status_code == 200
    assert "Nothing has been saved yet" in page
    assert "Luka Doncic" in page
    assert "57.7" in page
    assert "Kyle Lowry (PHI)" in page
    assert "Matched under another name" in page
    assert "GG Jackson" in page
    assert import_url(run, "confirm") in page
    assert "Import 2 players" in page


def test_someone_elses_preview_can_be_read_but_not_imported(
    client, superuser, players, staff_user, password
):
    run = preview_for(superuser, LUKA)
    staff_user.is_superuser = True
    staff_user.save(update_fields=["is_superuser"])
    client.login(email=staff_user.email, password=password)

    page = client.get(import_url(run)).content.decode()
    assert import_url(run, "confirm") not in page

    client.post(import_url(run, "confirm"))
    assert not PlayerSeasonStats.objects.exists()


def test_a_preview_without_matches_offers_no_import(admin_client, superuser, players):
    run = preview_for(superuser, line("Kyle Lowry", "PHI"))

    page = admin_client.get(import_url(run)).content.decode()

    assert "nothing to import" in page
    assert "disabled" in page


def test_an_old_preview_shows_as_expired(admin_client, superuser, players):
    run = preview_for(superuser, LUKA)
    SeasonStatsImport.objects.filter(pk=run.pk).update(
        created_at=timezone.now() - PREVIEW_LIFETIME - dt.timedelta(minutes=1)
    )

    page = admin_client.get(import_url(run)).content.decode()

    assert "expired without being imported" in page
    assert import_url(run, "confirm") not in page


def test_an_unknown_upload_is_not_found(admin_client):
    url = reverse("nba:season-stats-import", args=["00000000-0000-0000-0000-000000000000"])

    assert admin_client.get(url).status_code == 404


# --- confirm and discard --------------------------------------------------------------


def test_confirming_imports_and_shows_the_result(admin_client, superuser, players):
    run = preview_for(superuser, LUKA, SGA)

    response = admin_client.post(import_url(run, "confirm"), follow=True)

    assert PlayerSeasonStats.objects.count() == 2
    page = response.content.decode()
    assert "Imported 2 players for 2025-26." in page
    assert "These are now the stored 2025-26 stats." in page
    assert "Luka Doncic" in page


def test_confirming_twice_explains_instead_of_failing(admin_client, superuser, players):
    run = preview_for(superuser, LUKA)
    admin_client.post(import_url(run, "confirm"))

    response = admin_client.post(import_url(run, "confirm"), follow=True)

    assert response.status_code == 200
    assert "already been closed" in response.content.decode()
    assert PlayerSeasonStats.objects.count() == 1


def test_discarding_saves_nothing_and_returns_to_the_upload_page(admin_client, superuser, players):
    run = preview_for(superuser, LUKA)

    response = admin_client.post(import_url(run, "discard"), follow=True)

    run.refresh_from_db()
    assert run.status == SeasonStatsImport.Status.DISCARDED
    assert response.redirect_chain[-1][0] == UPLOAD
    assert "Nothing was saved." in response.content.decode()
    assert not PlayerSeasonStats.objects.exists()


def test_confirm_and_discard_only_accept_posts(admin_client, superuser, players):
    run = preview_for(superuser, LUKA)

    assert admin_client.get(import_url(run, "confirm")).status_code == 405
    assert admin_client.get(import_url(run, "discard")).status_code == 405
