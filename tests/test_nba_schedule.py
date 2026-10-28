"""The NBA schedule import: reading the feed, storing a season, counting games.

The fixture is a real response of the range endpoint (21 October to 20 November
2026), cut down to 40 games -- every Lakers game in it among them -- and to the
fields the import reads.
"""

import copy
import datetime as dt
import io
import json
from datetime import date
from itertools import pairwise
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from django.core.management import CommandError, call_command
from django.urls import reverse
from django.utils import timezone
from django_q.models import Schedule

from apps.fantasy.models import Season
from apps.nba import schedule
from apps.nba.models import ApiQuota, Game, NbaApiUsage, Team
from apps.nba.schedule import TALLY, load_season, parse_games, ranges, season_span, why_not_now
from apps.nba.services import PROVIDER, DailyApiLimitExceeded, MonthlyReserveReached, NbaApiError
from apps.nba.tasks import scheduled_nba_schedule_load

from .test_rotowire_news import FakeResponse, rapid_headers

FIXTURE = Path(__file__).parent / "fixtures" / "nba_schedule" / "range.json"
UTC = dt.UTC
# Between the fixture's played games and its scheduled ones.
NOW = dt.datetime(2026, 10, 28, 18, 0, tzinfo=UTC)


@pytest.fixture
def payload():
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def teams(db):
    call_command("seed_teams", verbosity=0)
    return {team.abbreviation: team for team in Team.objects.all()}


@pytest.fixture
def season(db):
    return Season.objects.create(
        label="2026-27",
        starts_on=date(2026, 10, 20),
        ends_on=date(2027, 4, 11),
        is_current=True,
    )


@pytest.fixture
def enabled(settings):
    settings.NBA_SCHEDULE_ENABLED = True
    settings.RAPID_API_KEY = "test-key"


def load(season, *payloads, now=NOW):
    return load_season(season, payloads=list(payloads), now=now)


# -- reading the feed ------------------------------------------------------------


def test_every_fixture_game_is_read(payload):
    games = parse_games(payload)

    assert len(games) == 40
    first = games[0]
    assert first.espn_id == "401909834"
    assert first.tip_off == dt.datetime(2026, 10, 21, 23, 0, tzinfo=UTC)
    assert (first.away_abbreviation, first.home_abbreviation) == ("ATL", "ORL")
    assert (first.away_espn_id, first.home_espn_id) == (1, 19)
    assert first.status == "STATUS_FINAL"
    assert first.is_regular_season


def test_the_nba_cup_note_is_kept(payload):
    notes = {game.note for game in parse_games(payload)}

    assert "NBA Cup - Group Play" in notes


@pytest.mark.parametrize("field", ["id", "date", "home", "away"])
def test_a_game_missing_what_places_it_is_skipped(payload, field, caplog):
    del payload["data"][0][field]

    games = parse_games(payload)

    assert len(games) == 39
    assert "could not read" in caplog.text


def test_a_preseason_game_is_not_regular_season(payload):
    payload["data"][0]["season"]["type"] = 1

    assert parse_games(payload)[0].is_regular_season is False


# -- which days to ask for -------------------------------------------------------


def test_a_season_takes_six_requests_of_at_most_31_days(season):
    windows = list(ranges(*season_span(season)))

    assert len(windows) == 6
    assert windows[0][0] == date(2026, 10, 19)
    assert windows[-1][1] == date(2027, 4, 12)
    assert all((last - first).days + 1 <= 31 for first, last in windows)
    for (_, last), (following, _) in pairwise(windows):
        assert following == last + dt.timedelta(days=1)


# -- storing ---------------------------------------------------------------------


def test_a_load_stores_every_game_with_our_teams(teams, season, payload):
    result = load(season, payload)

    assert (result.games, result.created, result.unmatched) == (40, 40, 0)
    game = Game.objects.get(espn_id="401909834")
    assert (game.away_team, game.home_team) == (teams["ATL"], teams["ORL"])
    assert game.season_year == 2027


def test_loading_again_adds_nothing(teams, season, payload):
    load(season, payload)

    result = load(season, payload)

    assert (result.created, result.updated, result.removed) == (0, 40, 0)
    assert Game.objects.count() == 40


def test_overlapping_windows_store_a_game_once(teams, season, payload):
    result = load(season, payload, copy.deepcopy(payload))

    assert result.games == 40
    assert Game.objects.count() == 40


def test_a_moved_game_keeps_its_row_and_gets_the_new_date(teams, season, payload):
    load(season, payload)
    moved = copy.deepcopy(payload)
    entry = moved["data"][-1]
    before = Game.objects.get(espn_id=entry["id"])
    entry["date"] = "2027-03-20T00:00Z"

    load(season, moved)

    after = Game.objects.get(espn_id=entry["id"])
    assert after.pk == before.pk
    assert after.tip_off == dt.datetime(2027, 3, 20, tzinfo=UTC)


def test_a_game_to_come_that_is_no_longer_listed_is_dropped(teams, season, payload):
    load(season, payload)
    upcoming = next(
        game for game in payload["data"] if game["status"]["name"] == "STATUS_SCHEDULED"
    )
    trimmed = copy.deepcopy(payload)
    trimmed["data"] = [game for game in trimmed["data"] if game["id"] != upcoming["id"]]

    result = load(season, trimmed)

    assert result.removed == 1
    assert not Game.objects.filter(espn_id=upcoming["id"]).exists()


def test_a_game_in_the_past_is_never_dropped(teams, season, payload):
    load(season, payload)
    played = payload["data"][0]
    trimmed = copy.deepcopy(payload)
    trimmed["data"] = trimmed["data"][1:]

    result = load(season, trimmed)

    assert result.removed == 0
    assert Game.objects.filter(espn_id=played["id"]).exists()


def test_without_an_espn_id_the_team_is_found_by_espn_s_code(teams, season, payload):
    Team.objects.update(espn_id=None)

    result = load(season, payload)

    assert result.unmatched == 0
    lakers_road = Game.objects.filter(home_team=teams["GSW"]).count()
    assert lakers_road == sum(1 for game in payload["data"] if game["home"]["abbreviation"] == "GS")


def test_a_game_with_an_unknown_team_is_skipped(teams, season, payload, caplog):
    payload["data"][0]["home"].update(id="99", abbreviation="XYZ")

    result = load(season, payload)

    assert result.unmatched == 1
    assert Game.objects.count() == 39
    assert "unknown team" in caplog.text


def test_a_load_without_a_current_season_refuses(db):
    with pytest.raises(NbaApiError, match="No current season"):
        load_season(payloads=[])


# -- counting --------------------------------------------------------------------


def lakers_games(payload):
    return [
        g
        for g in payload["data"]
        if "LAL" in (g["home"]["abbreviation"], g["away"]["abbreviation"])
    ]


def test_played_and_upcoming_are_read_off_the_clock(teams, season, payload):
    load(season, payload)
    lakers = Game.objects.for_team(teams["LAL"])
    expected_played = sum(
        1
        for game in lakers_games(payload)
        if dt.datetime.fromisoformat(game["date"].replace("Z", "+00:00"))
        <= NOW - dt.timedelta(hours=3)
    )

    assert lakers.played(NOW).count() == expected_played == 4
    assert lakers.upcoming(NOW).count() == len(lakers_games(payload)) - 4


def test_a_game_counts_as_played_three_hours_after_tip_off(teams, season, payload):
    load(season, payload)
    game = Game.objects.for_team(teams["LAL"]).upcoming(NOW).first()

    assert not game.is_played(game.tip_off + dt.timedelta(hours=2, minutes=59))
    assert game.is_played(game.tip_off + dt.timedelta(hours=3))


def test_a_postponed_game_is_neither_played_nor_to_come(teams, season, payload):
    load(season, payload)
    game = Game.objects.for_team(teams["LAL"]).played(NOW).first()
    game.status = "STATUS_POSTPONED"
    game.save()

    assert Game.objects.for_team(teams["LAL"]).played(NOW).count() == 3
    assert not game.is_played(NOW)


def test_preseason_games_do_not_count(teams, season, payload):
    load(season, payload)
    Game.objects.filter(espn_id=payload["data"][0]["id"]).update(is_regular_season=False)

    assert Game.objects.played(NOW).count() == Game.objects.filter(tip_off__lte=NOW).count() - 1


def test_a_game_knows_its_opponent_and_side(teams, season, payload):
    load(season, payload)
    game = Game.objects.get(espn_id="401909834")

    assert game.opponent_of(teams["ORL"]) == teams["ATL"]
    assert game.is_home_for(teams["ORL"])
    assert not game.is_home_for(teams["ATL"])


# -- the request -----------------------------------------------------------------


def test_fetch_asks_for_the_range_and_counts_on_its_own_tally(db, enabled, payload, monkeypatch):
    sent = []

    def fake_urlopen(request, timeout):
        sent.append(request)
        return FakeResponse(json.dumps(payload).encode(), rapid_headers(84, limit=90))

    monkeypatch.setattr(schedule, "urlopen", fake_urlopen)

    assert schedule.fetch_range(date(2026, 10, 21), date(2026, 11, 20)) == payload
    (request,) = sent
    assert request.full_url.endswith("/v2/games/range?from=2026-10-21&to=2026-11-20")
    assert request.get_header("X-rapidapi-key") == "test-key"
    assert request.get_header("X-rapidapi-host") == "api-basketball-nba.p.rapidapi.com"
    assert NbaApiUsage.objects.get(provider=TALLY).request_count == 1
    assert not NbaApiUsage.objects.filter(provider=PROVIDER).exists()
    assert ApiQuota.objects.get(provider=PROVIDER).remaining == 84


def test_fetch_while_switched_off_makes_no_request(db, enabled, settings, monkeypatch):
    settings.NBA_SCHEDULE_ENABLED = False
    monkeypatch.setattr(schedule, "urlopen", pytest.fail)

    with pytest.raises(schedule.ScheduleDisabled):
        schedule.fetch_range(date(2026, 10, 21), date(2026, 11, 20))

    assert not NbaApiUsage.objects.exists()


def test_fetch_below_the_reserve_makes_no_request(db, enabled, monkeypatch):
    now = timezone.now()
    ApiQuota.objects.create(
        provider=PROVIDER,
        limit=90,
        remaining=9,
        resets_at=now + dt.timedelta(days=3),
        reported_at=now,
    )
    monkeypatch.setattr(schedule, "urlopen", pytest.fail)

    with pytest.raises(MonthlyReserveReached):
        schedule.fetch_range(date(2026, 10, 21), date(2026, 11, 20))


def test_the_daily_limit_is_ten(db, settings):
    for _ in range(10):
        schedule._reserve_api_call()

    with pytest.raises(DailyApiLimitExceeded):
        schedule._reserve_api_call()


def test_fetch_rejects_a_response_without_games(db, enabled, monkeypatch):
    monkeypatch.setattr(
        schedule, "urlopen", lambda request, timeout: FakeResponse(b'{"message": "nope"}')
    )

    with pytest.raises(NbaApiError, match="unexpected"):
        schedule.fetch_range(date(2026, 10, 21), date(2026, 11, 20))


def test_a_load_that_fails_half_way_stores_nothing(teams, season, enabled, payload, monkeypatch):
    calls = []

    def flaky(first, last):
        calls.append(first)
        if len(calls) == 3:
            raise NbaApiError("NBA schedule returned HTTP 503.")
        return payload

    monkeypatch.setattr(schedule, "fetch_range", flaky)

    with pytest.raises(NbaApiError):
        load_season()

    assert len(calls) == 3
    assert not Game.objects.exists()


def test_a_full_load_asks_for_every_window(teams, season, enabled, payload, monkeypatch):
    asked = []
    monkeypatch.setattr(
        schedule, "fetch_range", lambda first, last: asked.append((first, last)) or payload
    )

    result = load_season()

    assert asked == list(ranges(*season_span(season)))
    assert result.games == 40


# -- when to load ----------------------------------------------------------------


def test_the_checks_come_in_order(season, settings):
    settings.NBA_SCHEDULE_ENABLED = False
    settings.RAPID_API_KEY = ""
    assert why_not_now() == "NBA_SCHEDULE_ENABLED is off"

    settings.NBA_SCHEDULE_ENABLED = True
    assert why_not_now() == "RAPID_API_KEY is not set"

    settings.RAPID_API_KEY = "test-key"
    assert why_not_now(today=date(2027, 4, 11)) is None
    assert why_not_now(today=date(2027, 4, 12)) == "the current season 2026-27 is over"
    assert why_not_now(force=True, today=date(2027, 4, 12)) is None

    now = timezone.now()
    ApiQuota.objects.create(
        provider=PROVIDER,
        limit=90,
        remaining=5,
        resets_at=now + dt.timedelta(days=3),
        reported_at=now,
    )
    assert why_not_now(today=date(2027, 1, 1)).startswith("only 5 RapidAPI requests left")

    season.delete()
    assert why_not_now(force=True) == "no current season is set up"


def test_the_switch_is_off_outside_production_by_default():
    from django.conf import settings

    assert settings.ENVIRONMENT != "production"
    assert settings.NBA_SCHEDULE_ENABLED is False


# -- the command -----------------------------------------------------------------


def test_the_command_loads_and_reports(teams, season, enabled, payload, monkeypatch):
    monkeypatch.setattr(schedule, "fetch_range", lambda first, last: payload)
    monkeypatch.setattr(
        "apps.nba.management.commands.import_nba_schedule.why_not_now",
        lambda force=False: None,
    )
    out = io.StringIO()

    call_command("import_nba_schedule", stdout=out)

    assert "NBA schedule loaded: 40 games, 40 new" in out.getvalue()


def test_the_command_refuses_after_the_season_and_names_force(db, enabled):
    Season.objects.create(
        label="2025-26", starts_on=date(2025, 10, 21), ends_on=date(2026, 4, 12), is_current=True
    )

    with pytest.raises(CommandError, match=r"is over\. Run with --force"):
        call_command("import_nba_schedule")


def test_the_command_does_not_name_force_where_it_cannot_help(season, settings):
    settings.NBA_SCHEDULE_ENABLED = False

    with pytest.raises(CommandError) as refused:
        call_command("import_nba_schedule")

    assert "--force" not in str(refused.value)


def test_check_reports_without_a_request(teams, season, enabled, payload, monkeypatch):
    load(season, payload)
    monkeypatch.setattr(schedule, "urlopen", pytest.fail)
    out = io.StringIO()

    call_command("import_nba_schedule", check=True, stdout=out)

    text = out.getvalue()
    assert "A load would be made: 6 requests for 2026-27." in text
    assert "Stored: 40 games for 2026-27, last loaded " in text
    assert "RapidAPI has not reported a count yet." in text
    assert not NbaApiUsage.objects.exists()


# -- copying between databases ---------------------------------------------------


def test_games_copy_to_a_database_whose_teams_have_other_ids(teams, season, payload, tmp_path):
    load(season, payload)
    dump = tmp_path / "games.json"
    call_command("dumpdata", "nba.Game", "--natural-foreign", "--output", str(dump), verbosity=0)
    Game.objects.all().delete()
    Team.objects.all().delete()
    call_command("seed_teams", verbosity=0)

    call_command("loaddata", str(dump), verbosity=0)

    game = Game.objects.get(espn_id="401909834")
    assert (game.away_team.abbreviation, game.home_team.abbreviation) == ("ATL", "ORL")
    assert Game.objects.count() == 40


def test_times_are_stored_as_the_feed_gives_them(teams, season, payload):
    load(season, payload)
    game = Game.objects.get(espn_id="401909834")

    assert timezone.localtime(game.tip_off, ZoneInfo("Europe/Berlin")).hour == 1


def test_the_admin_lists_a_team_s_games(admin_client, teams, season, payload):
    load(season, payload)

    listing = admin_client.get(reverse("admin:nba_game_changelist"), {"team": "LAL"})

    assert listing.status_code == 200
    assert listing.context["cl"].result_count == 17


# -- the yearly loads ------------------------------------------------------------


def test_the_two_yearly_loads_exist_after_migrate(db):
    loads = Schedule.objects.filter(func="apps.nba.tasks.scheduled_nba_schedule_load")

    assert sorted(loads.values_list("cron", flat=True)) == ["0 6 1 10 *", "0 6 20 12 *"]
    for load_schedule in loads:
        assert load_schedule.repeats == -1
        assert load_schedule.next_run > timezone.now()
        local = timezone.localtime(load_schedule.next_run, ZoneInfo("Europe/Berlin"))
        assert (local.month, local.day, local.hour) in {(10, 1, 6), (12, 20, 6)}


def test_the_task_skips_and_says_why(season, settings, monkeypatch):
    settings.NBA_SCHEDULE_ENABLED = False
    monkeypatch.setattr(schedule, "load_season", pytest.fail)

    assert scheduled_nba_schedule_load() == "skipped: NBA_SCHEDULE_ENABLED is off"


def test_the_task_loads_and_reports(teams, season, enabled, payload, monkeypatch):
    monkeypatch.setattr(schedule, "fetch_range", lambda first, last: payload)

    assert scheduled_nba_schedule_load().startswith("40 games, 40 new")


def test_a_failed_load_fails_the_task(season, enabled, monkeypatch):
    def broken(first, last):
        raise NbaApiError("NBA schedule returned HTTP 503.")

    monkeypatch.setattr(schedule, "fetch_range", broken)

    with pytest.raises(NbaApiError):
        scheduled_nba_schedule_load()
