"""Staff controls on the Reports page: start, publish, retry, rerun, settings."""

import pytest
from django.urls import reverse

from apps.reports import services
from apps.reports.models import Report, ReportRun, ReportSettings
from tests.reports_factories import FakeAi, make_run, make_season, make_world

pytestmark = pytest.mark.django_db


@pytest.fixture
def fake_ai(monkeypatch):
    fake = FakeAi()
    monkeypatch.setattr("apps.reports.ai.generate", fake)
    return fake


@pytest.fixture
def season():
    return make_season()


@pytest.fixture
def staff(client, staff_user):
    client.force_login(staff_user)
    return client


@pytest.fixture
def post(staff, django_capture_on_commit_callbacks):
    """POST as staff and let any queued report tasks run, as the worker would."""

    def post(url, data=None):
        with django_capture_on_commit_callbacks(execute=True):
            return staff.post(url, data or {})

    return post


def messages_of(response):
    """The messages a request left, newest last (redirects are not followed,
    so they pile up across a test's requests)."""
    return [str(message) for message in response.wsgi_request._messages]


def staff_urls(run):
    return [
        ("post", reverse("reports:start")),
        ("post", reverse("reports:settings")),
        ("post", reverse("reports:publish", args=[run.pk])),
        ("post", reverse("reports:retry", args=[run.pk])),
        ("post", reverse("reports:rerun", args=[run.pk])),
        ("get", reverse("reports:estimate")),
        ("get", reverse("reports:progress", args=[run.pk])),
    ]


def test_players_cannot_use_any_staff_control(client, user, season):
    run = make_run(season)
    client.force_login(user)
    for method, url in staff_urls(run):
        assert getattr(client, method)(url).status_code == 403, url
    assert "AI report runs" not in client.get(reverse("reports:index")).content.decode()


def test_staff_see_the_panel_open_while_there_is_no_report(staff, season):
    html = staff.get(reverse("reports:index")).content.decode()
    assert '<details id="staff"' in html and "open>" in html.split('<details id="staff"')[1][:200]
    assert "Start run" in html
    assert "GPT-6.1 Sol" in html


def test_starting_a_run(post, season, manager, fake_ai, staff_user):
    make_world(season, manager)
    response = post(
        reverse("reports:start"),
        {"model": "gpt-6.1-sol", "effort": "high", "label": "Try Sol", "scope": "active"},
    )
    run = ReportRun.objects.get()
    assert response.url == reverse("reports:run", args=[run.pk]) + "#staff"
    assert (run.model, run.effort, run.label) == ("gpt-6.1-sol", "high", "Try Sol")
    assert run.trigger == ReportRun.Trigger.MANUAL
    assert run.started_by == staff_user
    assert run.status == ReportRun.Status.SUCCEEDED
    assert not run.is_published
    assert "Run started with GPT-6.1 Sol" in messages_of(response)[0]


def test_a_run_for_chosen_rosters_or_the_briefing_alone(post, season, manager, fake_ai):
    _, rosters = make_world(season, manager, rosters=2)
    post(
        reverse("reports:start"),
        {"model": "gpt-6-luna", "effort": "low", "scope": "chosen", "rosters": [rosters[1].pk]},
    )
    post(reverse("reports:start"), {"model": "gpt-6-luna", "effort": "low", "scope": "briefing"})
    chosen, briefing = ReportRun.objects.order_by("created_at")
    assert [r.roster for r in chosen.reports.filter(kind=Report.Kind.ROSTER)] == [rosters[1]]
    assert briefing.is_briefing_only and briefing.reports.count() == 1


def test_choosing_rosters_means_choosing_at_least_one(post, season, manager, fake_ai):
    make_world(season, manager)
    response = post(
        reverse("reports:start"), {"model": "gpt-6-luna", "effort": "low", "scope": "chosen"}
    )
    assert not ReportRun.objects.exists()
    assert messages_of(response) == ["Choose at least one roster."]


def test_no_run_without_a_key(post, season, manager, fake_ai, settings):
    make_world(season, manager)
    settings.AI_BRIEFING_API_KEY = ""
    response = post(
        reverse("reports:start"), {"model": "gpt-6-luna", "effort": "low", "scope": "active"}
    )
    assert not ReportRun.objects.exists()
    assert "no OpenAI API key" in messages_of(response)[0]


def test_publishing_and_hiding(post, season, manager, fake_ai, staff_user):
    make_world(season, manager)
    post(reverse("reports:start"), {"model": "gpt-6-luna", "effort": "low", "scope": "active"})
    run = ReportRun.objects.get()

    post(reverse("reports:publish", args=[run.pk]), {"publish": "1"})
    run.refresh_from_db()
    assert run.is_published and run.published_by == staff_user

    response = post(reverse("reports:publish", args=[run.pk]), {"publish": "0"})
    run.refresh_from_db()
    assert not run.is_published
    assert "Hidden" in messages_of(response)[-1]


def test_an_unfinished_run_cannot_be_published(post, season):
    run = make_run(season)
    response = post(reverse("reports:publish", args=[run.pk]), {"publish": "1"})
    run.refresh_from_db()
    assert not run.is_published
    assert "once it has finished" in messages_of(response)[0]


def test_retrying_failed_reports(post, season, manager, fake_ai):
    make_world(season, manager)
    fake_ai.fail = lambda call: call.schema == "RosterAdvice"
    post(reverse("reports:start"), {"model": "gpt-6-luna", "effort": "low", "scope": "active"})
    run = ReportRun.objects.get()
    assert run.status == ReportRun.Status.PARTIAL

    fake_ai.fail = None
    response = post(reverse("reports:retry", args=[run.pk]))
    run.refresh_from_db()
    assert run.status == ReportRun.Status.SUCCEEDED
    assert messages_of(response)[-1] == "Retrying 1 failed report."


def test_a_rerun_with_another_model(post, season, manager, fake_ai):
    make_world(season, manager)
    post(reverse("reports:start"), {"model": "gpt-6-luna", "effort": "low", "scope": "active"})
    run = ReportRun.objects.get()
    response = post(
        reverse("reports:rerun", args=[run.pk]),
        {"model": "gpt-6.1-terra", "effort": "medium", "label": "Terra"},
    )
    rerun = ReportRun.objects.exclude(pk=run.pk).get()
    assert response.url == reverse("reports:run", args=[rerun.pk]) + "#staff"
    assert (rerun.model, rerun.effort, rerun.source_run) == ("gpt-6.1-terra", "medium", run)
    assert rerun.shared_input == run.shared_input


def test_saving_the_sunday_settings(post):
    post(
        reverse("reports:settings"),
        {"default_model": "gpt-6.1-sol", "default_effort": "high"},
    )
    settings_row = ReportSettings.load()
    assert settings_row.default_model == "gpt-6.1-sol"
    assert settings_row.default_effort == "high"
    assert settings_row.sunday_run_enabled is False  # an unticked box


def test_the_estimate_follows_the_form(staff, season, manager):
    make_world(season, manager, rosters=2)
    url = reverse("reports:estimate")
    html = staff.get(url, {"model": "gpt-6.1-sol", "effort": "medium"}).content.decode()
    assert "Estimated cost" in html and "2 rosters" in html
    html = staff.get(url, {"model": "gpt-6.1-sol", "effort": "low", "scope": "briefing"})
    assert "0 rosters" in html.content.decode()
    assert "Choose a model" in staff.get(url, {"model": "gpt-4o"}).content.decode()


def test_progress_while_running_and_a_reload_when_done(staff, season):
    run = make_run(season)
    Report.objects.create(run=run, kind=Report.Kind.BRIEFING, status=Report.Status.SUCCEEDED)
    Report.objects.create(run=run, kind=Report.Kind.ROSTER)
    response = staff.get(reverse("reports:progress", args=[run.pk]))
    assert "1 of 2 done" in response.content.decode()
    assert "HX-Refresh" not in response

    run.status = ReportRun.Status.SUCCEEDED
    run.save()
    response = staff.get(reverse("reports:progress", args=[run.pk]))
    assert response["HX-Refresh"] == "true"


def test_the_panel_lists_every_run_with_its_cost(post, staff, season, manager, fake_ai):
    make_world(season, manager)
    post(reverse("reports:start"), {"model": "gpt-6-luna", "effort": "low", "scope": "active"})
    run = ReportRun.objects.get()
    services.publish(run)
    html = staff.get(reverse("reports:index")).content.decode()
    assert "All runs this season" in html
    assert "2/2" in html
    assert "Published" in html
