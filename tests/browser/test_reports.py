"""Reports in a real browser: the report on every screen size in both themes,
and the staff controls. The AI is faked (tests/reports_factories.FakeAi), so
nothing reaches OpenAI. Screenshots land in tmp/screens/reports/."""

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from apps.nba.models import Game, Team
from apps.reports import schemas, services
from apps.reports.models import ReportRun
from tests.reports_factories import FakeAi

from .conftest import page_overflow, shot

FOLDER = "reports"
URL = reverse("reports:index")


@pytest.fixture
def fake_ai(monkeypatch):
    fake = FakeAi()
    fake.briefing = schemas.Briefing(
        headline="Buy the risers before Sunday's update",
        summary="Salaries move at 22:00. Three players are predicted to rise sharply, "
        "and two injuries open up minutes for their teammates.",
        items=[
            schemas.BriefingItem(
                kind=kind, player_refs=refs, text=f"{label}: the figures behind it, briefly."
            )
            for kind, refs, label in (
                ("injury_opening", ["P002", "P005"], "A starter is out for two weeks"),
                ("riser", ["P001"], "Scoring well above the season average"),
                ("faller", ["P003", "P004"], "Two regulars have cooled off"),
                ("salary_watch", ["P006"], "Predicted to rise by more than a million"),
                ("schedule", [], "Four teams play four times next week"),
            )
        ],
    )
    monkeypatch.setattr("apps.reports.ai.generate", fake)
    return fake


@pytest.fixture
def report(game, fake_ai):
    """A published report on the game's season, with a schedule so every
    player has a projection and the roster has trades to choose from."""
    teams = list(Team.objects.order_by("name"))
    now = timezone.now()
    for index in range(0, len(teams) - 1, 2):
        for days in (1, 3, 20):
            Game.objects.create(
                espn_id=f"g-{index}-{days}",
                season_year=game["season"].ends_on.year,
                tip_off=now + dt.timedelta(days=days),
                home_team=teams[index],
                away_team=teams[index + 1],
            )
    run = services.start_run(
        game["season"],
        model="gpt-6.1-sol",
        effort="medium",
        trigger=ReportRun.Trigger.SCHEDULED,
        label="Sol at medium",
    )
    run.refresh_from_db()
    assert run.is_published, run.status
    return run


@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
@pytest.mark.parametrize("size", ["phone", "tablet", "desktop"])
def test_the_report_fits_every_screen(report, game, open_page, size, dark):
    page = open_page(game["user"], URL, size=size, dark=dark)

    expect(
        page.get_by_role("heading", name="Buy the risers before Sunday's update")
    ).to_be_visible()
    expect(page.get_by_text("Minutes opening up")).to_be_visible()
    expect(page.get_by_role("heading", name="Bulla Ballers")).to_be_visible()
    expect(page.get_by_text("Trade this week")).to_be_visible()
    expect(page.get_by_text("Projected gain")).to_be_visible()
    assert page_overflow(page) == 0
    shot(page, FOLDER, f"report-{size}-{'dark' if dark else 'light'}")


def test_phones_fold_the_other_reports_away(report, game, open_page):
    services.rerun_with_model(report, model="gpt-6-luna", effort="low")
    services.publish(ReportRun.objects.exclude(pk=report.pk).get())

    page = open_page(game["user"], URL, size="phone")
    disclosure = page.get_by_text("2 published reports")
    expect(disclosure).to_be_visible()
    disclosure.click()
    expect(page.get_by_role("link", name="GPT-6.1 Sol").first).to_be_visible()
    assert page_overflow(page) == 0


@pytest.mark.parametrize("size", ["phone", "desktop"])
def test_the_staff_panel_fits_and_answers(report, game, open_page, size):
    page = open_page(game["staff"], URL, size=size)

    page.get_by_text("AI report runs").click()
    expect(page.get_by_role("heading", name="New run")).to_be_visible()
    # The estimate arrives over HTMX and follows the form.
    expect(page.locator("#run-estimate")).to_contain_text("Estimated cost")
    page.get_by_text("Choose rosters").click()
    expect(page.get_by_role("checkbox", name="Bulla Ballers · Bulla")).to_be_visible()
    page.get_by_text("Briefing only").first.click()
    expect(page.locator("#run-estimate")).to_contain_text("0 rosters")
    # No estimate still on its way when the test's database is emptied.
    page.wait_for_load_state("networkidle")
    assert page_overflow(page) == 0
    shot(page, FOLDER, f"staff-{size}")


def test_staff_start_a_run_and_publish_it(report, game, open_page, fake_ai):
    page = open_page(game["staff"], URL, size="desktop")
    page.get_by_text("AI report runs").click()
    page.get_by_text("GPT-6 Luna").first.click()
    page.get_by_placeholder("e.g. Sol at high effort").fill("Browser run")
    page.get_by_role("button", name="Start run").click()

    expect(page.get_by_text("Run started with GPT-6 Luna")).to_be_visible()
    expect(page.get_by_text("Not published: only staff can see this")).to_be_visible()
    run = ReportRun.objects.get(label="Browser run")
    assert run.model == "gpt-6-luna" and not run.is_published

    page.locator(f"form[action='{reverse('reports:publish', args=[run.pk])}'] button").click()
    expect(page.get_by_text("Published: every manager can read this report now.")).to_be_visible()
    page.wait_for_load_state("networkidle")
    run.refresh_from_db()
    assert run.is_published
