"""Starting and running AI report runs: `apps.reports.services`, with a fake AI.

Django-Q runs synchronously in tests (config/settings/dev.py), so once the
start's transaction commits, a whole run executes in-process.
"""

from decimal import Decimal

import pytest

from apps.reports import prompts, schemas, services
from apps.reports.models import RecommendedTrade, Report, ReportRun
from tests.reports_factories import FakeAi, at, make_run, make_season, make_world

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
def start(season, django_capture_on_commit_callbacks):
    """Start a run and let its queued tasks run, as the worker would."""

    def start(**options):
        options.setdefault("model", "gpt-6-luna")
        options.setdefault("effort", "low")
        options.setdefault("trigger", ReportRun.Trigger.MANUAL)
        with django_capture_on_commit_callbacks(execute=True):
            run = services.start_run(season, now=NOW, **options)
        run.refresh_from_db()
        return run

    return start


def test_a_run_generates_the_briefing_first_then_each_roster(season, manager, start, fake_ai):
    _, rosters = make_world(season, manager, rosters=2)

    run = start(label="  First try ")

    assert run.status == ReportRun.Status.SUCCEEDED
    assert run.label == "First try"
    assert run.finished_at is not None
    assert not run.is_published  # manual runs wait for staff
    assert [call.schema for call in fake_ai.calls] == ["Briefing", "RosterAdvice", "RosterAdvice"]
    # Every call starts with the same instructions and data: the cacheable prefix.
    assert {(call.preamble, call.shared_input) for call in fake_ai.calls} == {
        (fake_ai.calls[0].preamble, run.shared_input)
    }
    assert "SHORTLIST" in fake_ai.calls[1].task
    assert {call.model for call in fake_ai.calls} == {"gpt-6-luna"}
    reports = run.reports.all()
    assert {report.roster for report in reports} == {None, *rosters}
    assert all(report.status == Report.Status.SUCCEEDED for report in reports)


def test_answers_usage_and_cost_are_stored(season, manager, start, fake_ai):
    make_world(season, manager)
    run = start()

    briefing = run.reports.get(kind=Report.Kind.BRIEFING)
    assert briefing.output["headline"] == "A busy week"
    assert briefing.output["items"][0]["player_refs"] == ["P001"]
    assert briefing.cache_write_tokens == 9_000
    assert briefing.reasoning_tokens == 1_500
    # Luna: 1K fresh at $0.10, 9K written at $0.125, 2K out at $0.50 (per million).
    assert briefing.cost_usd == Decimal("0.002225")
    assert briefing.attempts == 1
    assert briefing.response_id == "resp_1"

    report = run.reports.get(kind=Report.Kind.ROSTER)
    assert report.output["decision"] == "trade"
    trade = RecommendedTrade.objects.get(report=report)
    first = report.shortlist[0]
    assert trade.trade_ref == "T01"
    assert str(trade.player_out_id) == first["out"]
    assert str(trade.player_in_id) == first["in"]
    assert trade.salary_in == Decimal(first["salary_in"])
    assert trade.confidence == "high"


def test_answers_are_held_to_our_own_lists(season, manager, start, fake_ai):
    make_world(season, manager)

    def answer(call):
        return schemas.RosterAdvice(
            headline="Do something",
            situation="",
            decision="trade",
            trades=[
                schemas.TradePick(
                    horizon="few_weeks",
                    why_it_lasts="His role grew.",
                    what_ends_it="The starter's return.",
                    trade_id="T99",
                    priority=1,
                    reasoning="",
                    confidence="low",
                )
            ],
            hold_reason="",
            watch=[schemas.WatchItem(player_ref="P999", text="Who?")],
            risks="",
        )

    fake_ai.answer = answer
    report = start().reports.get(kind=Report.Kind.ROSTER)

    assert report.status == Report.Status.SUCCEEDED
    assert report.output["decision"] == "hold"
    assert report.output["trades"] == []
    assert report.output["watch"] == []
    assert "T99" in report.error and "P999" in report.error
    assert not report.trades.exists()


def test_a_hold_drops_the_trades_it_also_listed(season, manager, start, fake_ai):
    make_world(season, manager)
    fake_ai.answer = lambda call: schemas.RosterAdvice(
        headline="Wait",
        situation="",
        decision="hold",
        trades=[
            schemas.TradePick(
                horizon="few_weeks",
                why_it_lasts="His role grew.",
                what_ends_it="The starter's return.",
                trade_id="T01",
                priority=1,
                reasoning="",
                confidence="low",
            )
        ],
        hold_reason="Nothing beats your bench.",
        watch=[],
        risks="",
    )
    report = start().reports.get(kind=Report.Kind.ROSTER)
    assert report.output["decision"] == "hold"
    assert report.output["hold_reason"] == "Nothing beats your bench."
    assert not report.trades.exists()


def test_a_failed_report_keeps_its_cost_and_leaves_the_run_partial(season, manager, start, fake_ai):
    make_world(season, manager, rosters=2)
    fake_ai.fail = lambda call: call.schema == "RosterAdvice" and len(fake_ai.calls) == 2

    run = start()

    assert run.status == ReportRun.Status.PARTIAL
    failed = run.reports.get(status=Report.Status.FAILED)
    assert failed.error == "The model declined: test"
    assert failed.cost_usd > 0
    assert failed.output is None


def test_only_failed_reports_are_retried(
    season, manager, start, fake_ai, django_capture_on_commit_callbacks
):
    make_world(season, manager, rosters=2)
    fake_ai.fail = lambda call: call.schema == "RosterAdvice" and len(fake_ai.calls) == 2
    run = start()
    fake_ai.fail = None
    calls_before = len(fake_ai.calls)

    with django_capture_on_commit_callbacks(execute=True):
        retried = services.retry_failed(run, now=NOW)

    run.refresh_from_db()
    assert retried == 1
    assert len(fake_ai.calls) == calls_before + 1
    assert run.status == ReportRun.Status.SUCCEEDED
    assert run.reports.get(attempts=2).status == Report.Status.SUCCEEDED


def test_a_succeeded_report_is_never_generated_twice(season, manager, start, fake_ai):
    make_world(season, manager)
    run = start()
    briefing = run.reports.get(kind=Report.Kind.BRIEFING)
    calls = len(fake_ai.calls)

    services.run_report(briefing.pk)

    assert len(fake_ai.calls) == calls
    briefing.refresh_from_db()
    assert briefing.attempts == 1


def test_an_automatic_run_publishes_itself_when_its_briefing_worked(
    season, manager, start, fake_ai
):
    make_world(season, manager)
    run = start(trigger=ReportRun.Trigger.SCHEDULED)
    assert run.is_published
    assert run.published_at == run.finished_at
    assert run.published_by is None


def test_an_automatic_run_without_a_briefing_stays_hidden(season, manager, start, fake_ai):
    make_world(season, manager)
    fake_ai.fail = lambda call: call.schema == "Briefing"
    run = start(trigger=ReportRun.Trigger.SCHEDULED)
    assert run.status == ReportRun.Status.PARTIAL
    assert not run.is_published


def test_briefing_only_runs_one_call(season, manager, start, fake_ai):
    make_world(season, manager)
    run = start(briefing_only=True)
    assert run.scope == ReportRun.Scope.BRIEFING_ONLY
    assert run.reports.count() == 1
    assert [call.schema for call in fake_ai.calls] == ["Briefing"]


def test_a_chosen_set_of_rosters(season, manager, start, fake_ai):
    _, rosters = make_world(season, manager, rosters=3)
    run = start(rosters=rosters[:1])
    assert list(run.reports.filter(kind=Report.Kind.ROSTER).values_list("roster", flat=True)) == [
        rosters[0].pk
    ]


def test_a_rerun_puts_the_same_input_to_another_model(
    season, manager, start, fake_ai, staff_user, django_capture_on_commit_callbacks
):
    make_world(season, manager)
    run = start()
    with django_capture_on_commit_callbacks(execute=True):
        rerun = services.rerun_with_model(
            run, model="gpt-6.1-sol", effort="high", started_by=staff_user, now=NOW
        )
    rerun.refresh_from_db()

    assert rerun.trigger == ReportRun.Trigger.RERUN
    assert rerun.source_run == run
    assert (rerun.model, rerun.effort) == ("gpt-6.1-sol", "high")
    assert rerun.shared_input == run.shared_input
    assert rerun.data_as_of == run.data_as_of
    old = run.reports.get(kind=Report.Kind.ROSTER)
    new = rerun.reports.get(kind=Report.Kind.ROSTER)
    assert (new.roster, new.own_input, new.shortlist) == (old.roster, old.own_input, old.shortlist)
    assert rerun.status == ReportRun.Status.SUCCEEDED
    assert fake_ai.calls[-1].model == "gpt-6.1-sol"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"AI_BRIEFING_API_KEY": ""}, "no OpenAI API key"),
        ({"AI_BRIEFING_MONTHLY_LIMIT_USD": "0"}, "spending limit"),
    ],
)
def test_no_run_without_a_key_or_past_the_limit(
    season, manager, settings, fake_ai, change, message
):
    make_world(season, manager)
    for name, value in change.items():
        setattr(settings, name, value)
    with pytest.raises(services.ReportsUnavailable, match=message):
        services.start_run(season, model="gpt-6-luna", effort="low", trigger="manual", now=NOW)
    assert not ReportRun.objects.exists()
    assert fake_ai.calls == []


def test_only_approved_models_and_efforts(season, manager, fake_ai):
    make_world(season, manager)
    with pytest.raises(services.ReportsUnavailable, match="not an approved model"):
        services.start_run(season, model="gpt-4o", effort="low", trigger="manual", now=NOW)
    with pytest.raises(services.ReportsUnavailable, match="effort"):
        services.start_run(season, model="gpt-6-luna", effort="max", trigger="manual", now=NOW)


def test_nothing_to_report_is_unavailable_too(season, fake_ai):
    with pytest.raises(services.ReportsUnavailable, match="no stats"):
        services.start_run(season, model="gpt-6-luna", effort="low", trigger="manual", now=NOW)


def test_publishing_needs_a_finished_run_with_an_answer(
    season, manager, start, fake_ai, staff_user
):
    unfinished = make_run(season)
    with pytest.raises(services.ReportsUnavailable):
        services.publish(unfinished, staff_user)

    make_world(season, manager)
    run = start()
    services.publish(run, staff_user, now=NOW)
    run.refresh_from_db()
    assert run.is_published and run.published_by == staff_user and run.published_at == NOW

    services.unpublish(run)
    run.refresh_from_db()
    assert not run.is_published


def test_codes_readers_never_see_become_names(season, manager, start, fake_ai):
    """The AI is told to name players; whatever code still slips into a text
    is replaced before it is stored, and noted for staff."""
    players, _ = make_world(season, manager)
    fake_ai.briefing = schemas.Briefing(
        headline="Watch P001",
        summary="P001 leads, and P099 is not ours.",
        items=[schemas.BriefingItem(kind="riser", player_refs=["P001"], text="P001 is hot.")],
    )
    fake_ai.answer = lambda call: schemas.RosterAdvice(
        headline="Make T01 now",
        situation="T01 adds the most; T77 is not on the list.",
        decision="trade",
        trades=[
            schemas.TradePick(
                horizon="few_weeks",
                why_it_lasts="His role grew.",
                what_ends_it="The starter's return.",
                trade_id="T01",
                priority=1,
                reasoning="T01 brings in P001.",
                confidence="high",
            )
        ],
        hold_reason="",
        watch=[schemas.WatchItem(player_ref="P001", text="Watch P001 closely.")],
        risks="",
    )

    run = start()

    star = players[-1].full_name  # the best scorer is P001
    briefing = run.reports.get(kind=Report.Kind.BRIEFING)
    assert briefing.output["headline"] == f"Watch {star}"
    assert briefing.output["summary"] == f"{star} leads, and P099 is not ours."
    assert briefing.output["items"][0]["text"] == f"{star} is hot."
    assert briefing.output["items"][0]["player_refs"] == ["P001"]  # ref fields stay codes
    assert briefing.error == "Named in place of codes: P001."

    report = run.reports.get(kind=Report.Kind.ROSTER)
    first = report.shortlist[0]
    out_name = next(p.full_name for p in players if str(p.pk) == first["out"])
    in_name = next(p.full_name for p in players if str(p.pk) == first["in"])
    swap = f"{out_name} → {in_name}"
    assert report.output["headline"] == f"Make {swap} now"
    assert report.output["situation"] == f"{swap} adds the most; T77 is not on the list."
    assert report.output["trades"][0]["trade_id"] == "T01"  # the id field stays a code
    assert report.output["trades"][0]["reasoning"] == f"{swap} brings in {star}."
    assert report.output["watch"] == [{"player_ref": "P001", "text": f"Watch {star} closely."}]
    assert report.trades.get().reasoning == f"{swap} brings in {star}."
    assert report.error == "Named in place of codes: P001, T01."


def test_an_answer_without_codes_is_stored_as_written(season, manager, start, fake_ai):
    make_world(season, manager)
    run = start()
    assert run.reports.get(kind=Report.Kind.ROSTER).error == ""
    assert run.prompt_version == prompts.VERSION


def test_the_horizon_and_its_reasons_are_stored_in_names(season, manager, start, fake_ai):
    players, _ = make_world(season, manager)
    fake_ai.answer = lambda call: schemas.RosterAdvice(
        headline="Buy the backup",
        situation="Room for one move.",
        decision="trade",
        trades=[
            schemas.TradePick(
                trade_id="T01",
                priority=1,
                horizon="few_weeks",
                reasoning="More points.",
                why_it_lasts="P001 starts while the star is out.",
                what_ends_it="T01 sours when the star returns on 2026-11-24.",
                confidence="medium",
            )
        ],
        hold_reason="",
        watch=[],
        risks="",
    )

    run = start()

    report = run.reports.get(kind=Report.Kind.ROSTER)
    star = players[-1].full_name
    (trade,) = report.output["trades"]
    assert trade["horizon"] == "few_weeks"
    assert trade["why_it_lasts"] == f"{star} starts while the star is out."
    assert "T01" not in trade["what_ends_it"] and "2026-11-24" in trade["what_ends_it"]
    assert report.trades.get().horizon == RecommendedTrade.Horizon.FEW_WEEKS


def test_an_opportunity_item_is_kept(season, manager, start, fake_ai):
    make_world(season, manager)
    fake_ai.briefing = schemas.Briefing(
        headline="Openings",
        summary="A starter is out.",
        items=[schemas.BriefingItem(kind="opportunity", player_refs=["P001"], text="Starts now.")],
    )

    run = start()

    items = run.reports.get(kind=Report.Kind.BRIEFING).output["items"]
    assert items[0]["kind"] == "opportunity"


def test_the_prompt_asks_for_weeks_not_days(season, manager, start, fake_ai):
    make_world(season, manager)

    start()

    preamble = fake_ai.calls[0].preamble
    assert "next three to six weeks" in preamble
    assert "never on its own a reason to trade" in preamble
    assert "WHO BENEFITS" in preamble and "RETURNED" in preamble
    roster_task = fake_ai.calls[1].task
    assert "horizon" in roster_task


def test_the_schema_requires_a_horizon():
    fields = schemas.TradePick.model_json_schema()["properties"]
    assert fields["horizon"]["enum"] == ["short_term", "few_weeks", "rest_of_season"]
    assert {"why_it_lasts", "what_ends_it"} <= set(
        schemas.TradePick.model_json_schema()["required"]
    )
