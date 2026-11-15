"""The Reports page: `apps.reports.views` and `apps.reports.pages`."""

import datetime as dt
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.fantasy.models import Manager, Transaction
from apps.reports import schemas, services
from apps.reports.models import Report, ReportRun
from tests.reports_factories import (
    LATEST,
    FakeAi,
    at,
    make_roster,
    make_season,
    make_world,
    snapshot,
)

pytestmark = pytest.mark.django_db

NOW = at(11, 15, 12, 30)


@pytest.fixture
def fake_ai(monkeypatch):
    fake = FakeAi()
    monkeypatch.setattr("apps.reports.ai.generate", fake)
    return fake


@pytest.fixture
def season():
    return make_season()


@pytest.fixture
def run_of(season, fake_ai, django_capture_on_commit_callbacks):
    def run_of(publish=True, **options):
        options.setdefault("model", "gpt-6-luna")
        options.setdefault("effort", "low")
        options.setdefault("trigger", ReportRun.Trigger.MANUAL)
        with django_capture_on_commit_callbacks(execute=True):
            run = services.start_run(season, now=NOW, **options)
        run.refresh_from_db()
        if publish:
            services.publish(run)
        return run

    return run_of


@pytest.fixture
def signed_in(client, user):
    client.force_login(user)
    return client


def test_signing_in_is_required(client):
    response = client.get(reverse("reports:index"))
    assert response.status_code == 302


def test_the_sidebar_links_to_reports(signed_in):
    html = signed_in.get(reverse("reports:index")).content.decode()
    assert f'href="{reverse("reports:index")}"' in html


def test_without_a_season(signed_in):
    response = signed_in.get(reverse("reports:index"))
    assert response.status_code == 200
    assert "No season is running" in response.content.decode()


def test_before_anything_is_published(signed_in, season, manager, run_of):
    make_world(season, manager)
    run_of(publish=False)
    html = signed_in.get(reverse("reports:index")).content.decode()
    assert "No report yet" in html


def test_opens_on_the_newest_published_run(signed_in, season, manager, run_of):
    make_world(season, manager)
    older = run_of(label="Older one")
    newer = run_of(label="Newer one")
    run_of(publish=False, label="Hidden one")

    response = signed_in.get(reverse("reports:index"))
    html = response.content.decode()

    assert response.context["run"] == newer
    assert [run.pk for run in response.context["runs"]] == [newer.pk, older.pk]
    assert "A busy week" in html
    assert "Hidden one" not in html
    assert reverse("reports:run", args=[older.pk]) in html


def test_players_never_see_an_unpublished_run(signed_in, season, manager, run_of):
    make_world(season, manager)
    hidden = run_of(publish=False)
    assert signed_in.get(reverse("reports:run", args=[hidden.pk])).status_code == 404


def test_staff_can_open_an_unpublished_run(client, staff_user, season, manager, run_of):
    make_world(season, manager)
    hidden = run_of(publish=False)
    client.force_login(staff_user)
    html = client.get(reverse("reports:run", args=[hidden.pk])).content.decode()
    assert "Not published: only staff can see this" in html


def test_a_player_sees_only_their_own_rosters(signed_in, season, manager, other_manager, run_of):
    _, rosters = make_world(season, manager)
    rival = make_roster(season, other_manager, rosters[0].current_players.all(), name="Rivals")
    run_of()

    response = signed_in.get(reverse("reports:index"))
    sections = response.context["sections"]

    assert [section.roster for section in sections] == rosters
    assert "Rivals" not in response.content.decode()
    assert response.context["roster_choices"] == []
    assert rival.reports.exists()


def test_staff_can_look_at_any_roster(client, staff_user, season, manager, other_manager, run_of):
    players, rosters = make_world(season, manager)
    rival = make_roster(season, other_manager, players[:5], name="Rivals")
    run_of()
    client.force_login(staff_user)

    response = client.get(reverse("reports:index"))
    assert {roster.pk for roster in response.context["roster_choices"]} == {
        rosters[0].pk,
        rival.pk,
    }
    assert len(response.context["sections"]) == 1  # no own roster: the first one

    response = client.get(reverse("reports:index") + f"?roster={rival.pk}")
    assert [section.roster for section in response.context["sections"]] == [rival]
    assert "Managed by Rival" in response.content.decode()


def test_a_trade_card_is_drawn_from_our_own_figures(signed_in, season, manager, run_of):
    _, rosters = make_world(season, manager)
    run = run_of()
    report = run.reports.get(roster=rosters[0])
    first = report.shortlist[0]

    response = signed_in.get(reverse("reports:index"))
    (card,) = response.context["sections"][0].trades
    html = response.content.decode()

    assert str(card.out.pk) == first["out"] and str(card.into.pk) == first["in"]
    assert card.cash_after == Decimal(first["cash_after"])
    assert "Trade this week" in html
    assert "High confidence" in html
    assert f"+{first['fp_gain']}" in html


def test_the_ais_words_are_escaped(signed_in, season, manager, run_of, fake_ai):
    make_world(season, manager)
    fake_ai.answer = lambda call: schemas.RosterAdvice(
        headline="<script>alert('x')</script>",
        situation="<b>bold</b>",
        decision="hold",
        trades=[],
        hold_reason="Wait.",
        watch=[],
        risks="",
    )
    run_of()
    html = signed_in.get(reverse("reports:index")).content.decode()
    assert "<script>alert" not in html
    assert "&lt;script&gt;alert" in html
    assert "&lt;b&gt;bold&lt;/b&gt;" in html
    assert "Keep your trade" in html


def test_a_failed_roster_report_says_so(signed_in, season, manager, run_of, fake_ai):
    make_world(season, manager)
    fake_ai.fail = lambda call: call.schema == "RosterAdvice"
    run_of()
    html = signed_in.get(reverse("reports:index")).content.decode()
    assert "the AI could not write one" in html


def test_the_track_record_follows_a_trade_for_a_week(signed_in, season, manager, run_of):
    _, rosters = make_world(season, manager)
    first = run_of()
    trade = Report.objects.get(run=first, roster=rosters[0]).trades.get()
    # Three days on: the newcomer scored 60, the player sent away 10.
    later = LATEST + dt.timedelta(days=3)
    for player, gained in ((trade.player_in, 60), (trade.player_out, 10)):
        snapshot(season, player, later, total=player.current_total_fp + gained, games=9)
    Transaction.objects.create(
        roster=rosters[0],
        occurred_at=NOW + dt.timedelta(hours=1),
        kind=Transaction.Kind.TRADE,
        player_out=trade.player_out,
        player_in=trade.player_in,
        cash_delta=0,
    )

    (outcome,) = signed_in.get(reverse("reports:index")).context["sections"][0].outcomes
    assert (outcome.gained, outcome.days, outcome.followed) == (Decimal(50), 3, True)

    # The next report shows how the last one's advice did.
    second = run_of()
    response = signed_in.get(reverse("reports:index"))
    section = response.context["sections"][0]
    assert response.context["run"] == second
    assert section.previous_run == first
    assert [outcome.gained for outcome in section.previous] == [Decimal(50)]
    html = response.content.decode()
    assert "+50 FP" in html and "Trade made" in html


def test_too_early_to_tell(signed_in, season, manager, run_of):
    make_world(season, manager)
    run_of()
    html = signed_in.get(reverse("reports:index")).content.decode()
    assert "Too early to tell" in html


def test_queries_do_not_grow_with_rosters(client, user, season, run_of):
    manager = Manager.objects.create(user=user, nick_name="Many")
    players, _ = make_world(season, manager)
    make_roster(season, manager, players[1:6])
    make_roster(season, manager, players[2:7])
    run_of()
    client.force_login(user)
    with CaptureQueriesContext(connection) as three:
        response = client.get(reverse("reports:index"))
    assert len(response.context["sections"]) == 3

    for roster in response.context["sections"][1:]:
        roster.roster.delete()
    with CaptureQueriesContext(connection) as one:
        response = client.get(reverse("reports:index"))
    assert len(response.context["sections"]) == 1
    assert len(three) == len(one)
