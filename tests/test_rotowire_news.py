"""The RotoWire news import: parsing the feed, matching players, never storing twice.

The fixture is a real pull of the NBA feed (25 items from October 27, 2026).
"""

import copy
import datetime as dt
import io
import json
from datetime import date
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError

import pytest
from django.core.management import CommandError, call_command
from django.urls import reverse
from django.utils import timezone
from django_q.models import Schedule

from apps.fantasy.models import Season
from apps.nba import rotowire, tasks
from apps.nba.models import ApiQuota, NbaApiUsage, Player, PlayerNews, Team
from apps.nba.rotowire import (
    PROVIDER,
    RotoWireDisabled,
    fetch_news,
    name_key,
    parse_feed_date,
    parse_news,
    sync_news,
)
from apps.nba.services import DailyApiLimitExceeded, NbaApiError
from apps.nba.tasks import scheduled_rotowire_news_import

FIXTURE = Path(__file__).parent / "fixtures" / "rotowire" / "news_nba.json"


@pytest.fixture
def payload():
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def teams(db):
    call_command("seed_teams", verbosity=0)


def make_player(first_name, last_name, team=None, position="F", **fields):
    return Player.objects.create(
        first_name=first_name,
        last_name=last_name,
        position=position,
        team=Team.objects.get(abbreviation=team) if team else None,
        **fields,
    )


def item(payload, player):
    """The fixture's only response, cut down to the items about `player`."""
    trimmed = copy.deepcopy(payload)
    trimmed["items"] = [entry for entry in payload["items"] if entry["player"] == player]
    assert trimmed["items"], player
    return trimmed


# -- parsing -------------------------------------------------------------------


def test_every_fixture_item_is_parsed_in_feed_order(payload):
    items = parse_news(payload)

    assert len(items) == 25
    first = items[0]
    assert first.rotowire_news_id == 536367
    assert first.kind == PlayerNews.Kind.INJURY
    assert first.feed_player_id == 5681
    assert first.feed_player_name == "Khalifa Diop"
    assert first.feed_team_abbreviation == "CLE"
    assert first.feed_position == "C"
    assert first.headline == "Remains out for Wednesday"
    assert first.published_on == date(2026, 10, 27)


def test_the_kind_comes_from_the_article_link(payload):
    kinds = {entry.feed_player_name: entry.kind for entry in parse_news(payload)}

    assert kinds["Branden Carlson"] == PlayerNews.Kind.NEWS
    assert kinds["Marcus Smart"] == PlayerNews.Kind.INJURY


def test_the_team_comes_from_its_code_not_the_glued_team_name(payload):
    lendeborg = next(e for e in parse_news(payload) if e.feed_player_name == "Yaxel Lendeborg")

    assert payload["items"][3]["team"] == "Golden State WarriorsFoot"
    assert lendeborg.feed_team_abbreviation == "GSW"


def test_rotowire_team_codes_are_translated_to_ours(payload):
    booker = next(e for e in parse_news(payload) if e.feed_player_name == "Devin Booker")

    assert booker.feed_team_abbreviation == "PHO"
    assert booker.team_code == "PHX"


def test_an_item_without_an_id_in_its_link_is_skipped(payload, caplog):
    payload["items"][0]["headlineUrl"] = "https://www.rotowire.com/basketball/headlines/"

    items = parse_news(payload)

    assert len(items) == 24
    assert 536367 not in {entry.rotowire_news_id for entry in items}
    assert "no ID in its link" in caplog.text


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)//www.rotowire.com/basketball/headlines/x-news-y-536399",
        "https://evil.example/basketball/headlines/x-news-y-536399",
        "http://www.rotowire.com/basketball/headlines/x-news-y-536399",
        "https://www.rotowire.com.evil.example/basketball/headlines/x-news-y-536399",
    ],
)
def test_a_link_that_is_not_rotowire_s_is_skipped(payload, url):
    payload["items"][0]["headlineUrl"] = url

    assert 536367 not in {entry.rotowire_news_id for entry in parse_news(payload)}


def test_a_headline_ending_in_a_number_keeps_the_items_id():
    entry = {
        "player": "Jerami Grant",
        "headline": "Scores 30",
        "headlineUrl": "https://www.rotowire.com/basketball/headlines/jerami-grant-news-scores-30-536399",
        "timestamp": "October 27, 2026",
    }

    (parsed,) = parse_news({"items": [entry]})

    assert parsed.rotowire_news_id == 536399


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("October 27, 2026", date(2026, 10, 27)),
        ("march 3, 2027", date(2027, 3, 3)),
        ("February 30, 2027", None),
        ("27.10.2026", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_feed_date(value, expected):
    assert parse_feed_date(value) == expected


def test_an_undated_item_is_dated_today():
    entry = {
        "player": "Jerami Grant",
        "headline": "Chips in",
        "headlineUrl": "https://www.rotowire.com/basketball/headlines/jerami-grant-news-chips-in-1",
        "timestamp": "yesterday",
    }

    (parsed,) = parse_news({"items": [entry]})

    assert parsed.published_on == timezone.localdate()


@pytest.mark.parametrize(
    ("ours", "theirs"),
    [
        ("Jaren Jackson Jr.", "Jaren Jackson"),
        ("A.J. Green", "AJ Green"),
        ("Luka Dončić", "Luka Doncic"),
        ("Marvin Bagley III", "Marvin Bagley"),
        ("De'Aaron Fox", "DeAaron Fox"),
        ("Shai Gilgeous-Alexander", "Shai Gilgeous-Alexander"),
    ],
)
def test_name_key_agrees_on_the_same_player(ours, theirs):
    assert name_key(ours) == name_key(theirs)


def test_name_key_keeps_different_players_apart():
    assert name_key("Jalen Johnson") != name_key("Jalen Johnston")


# -- storing ---------------------------------------------------------------------


def test_a_pull_stores_every_item(db, payload):
    result = sync_news(payload)

    assert PlayerNews.objects.count() == 25
    assert (result.items, result.created, result.updated) == (25, 25, 0)
    stored = PlayerNews.objects.get(rotowire_news_id=536364)
    assert stored.headline == "Iffy for Wednesday"
    assert stored.body.startswith("Lendeborg (foot) is questionable")
    assert stored.url.endswith("-536364")
    assert stored.feed_team_abbreviation == "GSW"
    assert stored.raw_payload["player"] == "Yaxel Lendeborg"


def test_pulling_the_same_items_again_adds_nothing(db, payload):
    sync_news(payload)
    first_seen = dict(PlayerNews.objects.values_list("rotowire_news_id", "first_seen_at"))

    result = sync_news(payload)

    assert PlayerNews.objects.count() == 25
    assert (result.created, result.updated) == (0, 25)
    # When an item was first seen does not move on a second pull.
    assert dict(PlayerNews.objects.values_list("rotowire_news_id", "first_seen_at")) == first_seen


def test_an_overlapping_pull_only_adds_the_new_items(db, payload):
    older = copy.deepcopy(payload)
    older["items"] = payload["items"][3:]
    sync_news(older)

    result = sync_news(payload)

    assert (result.created, result.updated) == (3, 22)
    assert PlayerNews.objects.count() == 25


def test_a_corrected_headline_updates_the_item_in_place(db, payload):
    sync_news(payload)
    payload["items"][0]["headline"] = "Out again Wednesday"

    sync_news(payload)

    assert PlayerNews.objects.count() == 25
    assert PlayerNews.objects.get(rotowire_news_id=536367).headline == "Out again Wednesday"


# -- matching --------------------------------------------------------------------


def test_a_player_is_matched_by_name_and_team_and_keeps_rotowire_s_id(teams, payload):
    smart = make_player("Marcus", "Smart", "HOU", position="G")

    result = sync_news(item(payload, "Marcus Smart"))

    assert PlayerNews.objects.get().player == smart
    smart.refresh_from_db()
    assert smart.rotowire_id == 3575
    assert (result.matched, result.unmatched) == (1, 0)


def test_a_suffix_on_our_side_still_matches(teams, payload):
    jackson = make_player("Jaren", "Jackson Jr.", "UTA")

    sync_news(item(payload, "Jaren Jackson"))

    assert PlayerNews.objects.get().player == jackson


def test_rotowire_s_phoenix_code_matches_our_suns(teams, payload):
    booker = make_player("Devin", "Booker", "PHX", position="G")

    sync_news(item(payload, "Devin Booker"))

    assert PlayerNews.objects.get().player == booker


def test_the_name_alone_matches_when_the_feed_has_an_old_team(teams, payload):
    # The feed still lists Freeman as a free agent after he re-signed.
    freeman = make_player("Enrique", "Freeman", "MIN")

    sync_news(item(payload, "Enrique Freeman"))

    assert PlayerNews.objects.get().player == freeman


def test_the_same_team_decides_between_two_players_of_one_name(teams, payload):
    make_player("Marcus", "Smart", "BOS", position="G")
    houston = make_player("Marcus", "Smart", "HOU", position="G")

    sync_news(item(payload, "Marcus Smart"))

    assert PlayerNews.objects.get().player == houston


def test_two_players_of_one_name_elsewhere_link_neither(teams, payload):
    make_player("Marcus", "Smart", "BOS", position="G")
    make_player("Marcus", "Smart", "LAL", position="G")

    result = sync_news(item(payload, "Marcus Smart"))

    assert PlayerNews.objects.get().player is None
    assert not Player.objects.filter(rotowire_id__isnull=False).exists()
    assert result.unmatched == 1


def test_an_inactive_player_is_not_matched_by_name(teams, payload):
    make_player("Marcus", "Smart", "HOU", position="G", is_active=False)

    sync_news(item(payload, "Marcus Smart"))

    assert PlayerNews.objects.get().player is None


def test_a_stored_rotowire_id_wins_over_name_and_team(teams, payload):
    # Traded, and spelled differently here: only the ID still connects them.
    smart = make_player("Marcus", "Smart-Jones", "LAL", position="G", rotowire_id=3575)

    sync_news(item(payload, "Marcus Smart"))

    assert PlayerNews.objects.get().player == smart


def test_a_namesake_who_carries_another_rotowire_id_is_not_matched(teams, payload):
    make_player("Marcus", "Smart", "HOU", position="G", rotowire_id=9999)

    sync_news(item(payload, "Marcus Smart"))

    assert PlayerNews.objects.get().player is None


def test_one_match_links_every_item_about_that_player(teams, payload):
    sengun = make_player("Alperen", "Sengun", "HOU", position="C")

    sync_news(item(payload, "Alperen Sengun"))

    assert PlayerNews.objects.count() == 2
    assert set(PlayerNews.objects.values_list("player", flat=True)) == {sengun.pk}


def test_an_unmatched_item_is_linked_once_its_player_exists(teams, payload):
    sync_news(item(payload, "Marcus Smart"))
    assert PlayerNews.objects.get().player is None
    smart = make_player("Marcus", "Smart", "HOU", position="G")

    result = sync_news({"items": []})

    assert PlayerNews.objects.get().player == smart
    assert result.relinked == 1


def test_a_link_set_by_hand_survives_a_pull_that_cannot_match(teams, payload):
    sync_news(item(payload, "Marcus Smart"))
    by_hand = make_player("Marcus", "Smart", "BOS", position="G")
    make_player("Marcus", "Smart", "LAL", position="G")
    PlayerNews.objects.update(player=by_hand)

    sync_news(item(payload, "Marcus Smart"))

    assert PlayerNews.objects.get().player == by_hand


# -- the request -----------------------------------------------------------------


def rapid_headers(remaining, limit=500, reset=86400):
    """Response headers as RapidAPI sends them; they look up case-blind."""
    headers = Message()
    for name, value in [
        ("X-RateLimit-Requests-Limit", limit),
        ("X-RateLimit-Requests-Remaining", remaining),
        ("X-RateLimit-Requests-Reset", reset),
    ]:
        if value is not None:
            headers[name] = str(value)
    return headers


class FakeResponse(io.BytesIO):
    status = 200

    def __init__(self, body, headers=None):
        super().__init__(body)
        self.headers = headers if headers is not None else Message()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def api_key(settings):
    settings.RAPID_API_KEY = "test-key"


def test_fetch_sends_the_key_and_counts_the_request(db, api_key, payload, monkeypatch):
    sent = []

    def fake_urlopen(request, timeout):
        sent.append(request)
        return FakeResponse(json.dumps(payload).encode())

    monkeypatch.setattr(rotowire, "urlopen", fake_urlopen)

    assert fetch_news() == payload
    (request,) = sent
    assert request.full_url.endswith("/api/v1/news/nba")
    assert request.get_header("X-rapidapi-key") == "test-key"
    assert request.get_header("X-rapidapi-host") == "rotowire-fantasy-sports-api.p.rapidapi.com"
    assert NbaApiUsage.objects.get(provider=PROVIDER).request_count == 1


def test_a_pull_that_fails_to_store_still_counts_its_request(db, api_key, payload, monkeypatch):
    monkeypatch.setattr(
        rotowire, "urlopen", lambda request, timeout: FakeResponse(json.dumps(payload).encode())
    )

    def broken(items):
        raise RuntimeError("disk full")

    monkeypatch.setattr(rotowire, "_store", broken)

    with pytest.raises(RuntimeError):
        sync_news()

    assert NbaApiUsage.objects.get(provider=PROVIDER).request_count == 1


def test_fetch_without_a_key_makes_no_request(db, settings):
    settings.RAPID_API_KEY = ""

    with pytest.raises(NbaApiError):
        fetch_news()

    assert not NbaApiUsage.objects.exists()


def test_fetch_while_switched_off_makes_no_request(db, api_key, settings, monkeypatch):
    settings.ROTOWIRE_ENABLED = False
    monkeypatch.setattr(rotowire, "urlopen", pytest.fail)

    with pytest.raises(RotoWireDisabled):
        fetch_news()

    assert not NbaApiUsage.objects.exists()


def test_fetch_rejects_a_response_without_items(db, api_key, monkeypatch):
    monkeypatch.setattr(
        rotowire, "urlopen", lambda request, timeout: FakeResponse(b'{"message": "nope"}')
    )

    with pytest.raises(NbaApiError):
        fetch_news()


def test_the_daily_limit_refuses_the_next_request(db, settings):
    settings.ROTOWIRE_DAILY_LIMIT = 8
    for _ in range(8):
        rotowire._reserve_api_call()

    with pytest.raises(DailyApiLimitExceeded):
        rotowire._reserve_api_call()

    assert NbaApiUsage.objects.get(provider=PROVIDER).request_count == 8


def test_the_tally_is_separate_from_the_injury_feed(db):
    NbaApiUsage.objects.create(
        provider="api-basketball-nba", usage_date=timezone.localdate(), request_count=99
    )

    rotowire._reserve_api_call()

    assert NbaApiUsage.objects.get(provider=PROVIDER).request_count == 1


# -- the season window ----------------------------------------------------------


def make_season(label, starts_on, ends_on, is_current=False):
    return Season.objects.create(
        label=label, starts_on=starts_on, ends_on=ends_on, is_current=is_current
    )


@pytest.fixture
def in_season(db):
    today = timezone.localdate()
    return make_season("now", today - dt.timedelta(days=30), today + dt.timedelta(days=30), True)


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2026, 10, 5), False),
        (date(2026, 10, 6), True),
        (date(2027, 1, 15), True),
        (date(2027, 6, 20), True),
        (date(2027, 6, 21), False),
    ],
)
def test_the_window_runs_two_weeks_before_to_ten_after_a_season(db, today, expected):
    make_season("2026-27", date(2026, 10, 20), date(2027, 4, 11), is_current=True)

    assert rotowire.in_news_window(today) is expected


def test_without_a_season_there_is_no_window(db):
    assert rotowire.in_news_window() is False


def test_next_season_opens_the_window_before_it_is_made_current(db):
    make_season("2026-27", date(2026, 10, 20), date(2027, 4, 11), is_current=True)
    make_season("2027-28", date(2027, 10, 19), date(2028, 4, 9))

    assert rotowire.in_news_window(date(2027, 10, 10)) is True


def test_outside_the_window_the_task_does_nothing(db, api_key, monkeypatch):
    monkeypatch.setattr(tasks, "sync_news", pytest.fail)

    result = scheduled_rotowire_news_import()

    assert result.startswith("skipped: outside the season window (14 days before to 70 days after")


def test_outside_the_window_the_command_refuses(db, api_key, monkeypatch):
    monkeypatch.setattr(rotowire, "urlopen", pytest.fail)

    with pytest.raises(CommandError, match="outside the season window"):
        call_command("import_rotowire_news")


# -- RapidAPI's monthly requests ------------------------------------------------


def quota(remaining, resets_in=dt.timedelta(days=10), reported_ago=dt.timedelta(0), limit=500):
    now = timezone.now()
    return ApiQuota.objects.create(
        provider=PROVIDER,
        limit=limit,
        remaining=remaining,
        resets_at=now + resets_in if resets_in is not None else None,
        reported_at=now - reported_ago,
    )


def test_a_response_records_rapidapi_s_count(db, api_key, payload, monkeypatch):
    body = json.dumps(payload).encode()
    monkeypatch.setattr(
        rotowire,
        "urlopen",
        lambda request, timeout: FakeResponse(body, rapid_headers(484, reset=2603569)),
    )

    fetch_news()

    recorded = ApiQuota.objects.get(provider=PROVIDER)
    assert (recorded.limit, recorded.remaining) == (500, 484)
    expected = timezone.now() + dt.timedelta(seconds=2603569)
    assert abs(recorded.resets_at - expected) < dt.timedelta(minutes=1)


def test_the_next_response_overwrites_the_count(db, api_key, payload, monkeypatch):
    body = json.dumps(payload).encode()
    for remaining in (484, 483):
        monkeypatch.setattr(
            rotowire,
            "urlopen",
            lambda request, timeout, left=remaining: FakeResponse(body, rapid_headers(left)),
        )
        fetch_news()

    assert ApiQuota.objects.get().remaining == 483


def test_a_refused_request_records_the_count_too(db, api_key, monkeypatch):
    def refused(request, timeout):
        raise HTTPError(request.full_url, 429, "Too Many Requests", rapid_headers(0), None)

    monkeypatch.setattr(rotowire, "urlopen", refused)

    with pytest.raises(NbaApiError, match="429"):
        fetch_news()

    assert ApiQuota.objects.get().remaining == 0


@pytest.mark.parametrize("value", [None, "", "lots", "-1"])
def test_a_missing_or_odd_count_is_not_recorded(db, api_key, payload, monkeypatch, value):
    headers = Message()
    if value is not None:
        headers["X-RateLimit-Requests-Remaining"] = value
    body = json.dumps(payload).encode()
    monkeypatch.setattr(rotowire, "urlopen", lambda request, timeout: FakeResponse(body, headers))

    fetch_news()

    assert not ApiQuota.objects.exists()


def test_without_a_reset_time_the_count_still_records(db, api_key, payload, monkeypatch):
    body = json.dumps(payload).encode()
    monkeypatch.setattr(
        rotowire,
        "urlopen",
        lambda request, timeout: FakeResponse(body, rapid_headers(12, limit=None, reset=None)),
    )

    fetch_news()

    recorded = ApiQuota.objects.get()
    assert (recorded.limit, recorded.remaining, recorded.resets_at) == (None, 12, None)


def test_no_count_yet_is_no_reason_to_stop(db):
    assert rotowire.low_quota() is None


@pytest.mark.parametrize(("remaining", "low"), [(19, True), (20, False), (484, False)])
def test_the_reserve_is_twenty(db, remaining, low):
    quota(remaining)

    assert (rotowire.low_quota() is not None) is low


def test_a_count_from_before_the_reset_is_out_of_date(db):
    quota(0, resets_in=-dt.timedelta(minutes=1))

    assert rotowire.low_quota() is None


def test_without_a_reset_time_a_count_holds_for_its_calendar_month(db):
    quota(0, resets_in=None)
    now = timezone.now()

    assert rotowire.low_quota(now) is not None
    assert rotowire.low_quota(now + dt.timedelta(days=40)) is None


def test_with_too_few_requests_left_the_task_does_nothing(in_season, api_key, monkeypatch):
    quota(5)
    monkeypatch.setattr(tasks, "sync_news", pytest.fail)

    result = scheduled_rotowire_news_import()

    assert result.startswith("skipped: only 5 RapidAPI requests left until ")
    assert result.endswith("below the reserve of 20")


def test_the_admin_shows_the_count(admin_client):
    quota(484)

    page = admin_client.get(reverse("admin:nba_apiquota_changelist"))

    assert page.status_code == 200
    assert b"484" in page.content


# -- schedule, task and command --------------------------------------------------


def test_the_schedule_exists_after_migrate(db):
    schedule = Schedule.objects.get(func="apps.nba.tasks.scheduled_rotowire_news_import")

    assert schedule.schedule_type == Schedule.CRON
    assert schedule.cron == "0 6,12,18 * * *"
    assert schedule.repeats == -1
    assert not schedule.args
    assert not schedule.kwargs


def test_the_next_run_is_the_next_of_six_noon_and_six_berlin_time():
    from importlib import import_module
    from zoneinfo import ZoneInfo

    migration = import_module("apps.nba.migrations.0020_rotowire_news_three_times_a_day")

    next_run = migration.next_pull().astimezone(ZoneInfo("Europe/Berlin"))

    assert next_run.hour in (6, 12, 18)
    assert (next_run.minute, next_run.second) == (0, 0)
    assert dt.timedelta(0) < next_run - timezone.now() <= dt.timedelta(hours=12)


def test_without_a_key_the_task_does_nothing(db, settings, monkeypatch):
    settings.RAPID_API_KEY = ""
    monkeypatch.setattr(tasks, "sync_news", pytest.fail)

    assert scheduled_rotowire_news_import().startswith("skipped")


def test_while_switched_off_the_task_does_nothing(in_season, api_key, settings, monkeypatch):
    settings.ROTOWIRE_ENABLED = False
    monkeypatch.setattr(tasks, "sync_news", pytest.fail)

    assert scheduled_rotowire_news_import() == "skipped: ROTOWIRE_ENABLED is off"


def test_the_task_imports_and_reports(in_season, api_key, payload, monkeypatch):
    monkeypatch.setattr(rotowire, "fetch_news", lambda: payload)

    result = scheduled_rotowire_news_import()

    assert result.startswith("25 items, 25 new")
    assert PlayerNews.objects.count() == 25


def test_the_task_warns_when_every_item_was_new(in_season, api_key, payload, monkeypatch, caplog):
    older = copy.deepcopy(payload)
    older["items"] = [payload["items"][0]]
    sync_news(older)
    newer = copy.deepcopy(payload)
    newer["items"] = payload["items"][1:]
    monkeypatch.setattr(rotowire, "fetch_news", lambda: newer)

    scheduled_rotowire_news_import()

    assert "some may have been missed" in caplog.text


def test_the_task_does_not_warn_on_an_overlapping_pull(
    in_season, api_key, payload, monkeypatch, caplog
):
    sync_news(payload)
    monkeypatch.setattr(rotowire, "fetch_news", lambda: payload)

    scheduled_rotowire_news_import()

    assert "some may have been missed" not in caplog.text


def test_a_failed_request_fails_the_task(in_season, api_key, monkeypatch):
    def broken():
        raise NbaApiError("RotoWire API returned HTTP 503.")

    monkeypatch.setattr(rotowire, "fetch_news", broken)

    with pytest.raises(NbaApiError):
        scheduled_rotowire_news_import()


def test_the_command_imports_and_reports(in_season, api_key, payload, monkeypatch):
    monkeypatch.setattr(rotowire, "fetch_news", lambda: payload)
    out = io.StringIO()

    call_command("import_rotowire_news", stdout=out)

    assert "RotoWire news imported: 25 items, 25 new" in out.getvalue()


def test_the_command_turns_an_api_error_into_a_command_error(in_season, api_key, monkeypatch):
    def broken():
        raise DailyApiLimitExceeded("The rotowire daily limit of 8 requests has been reached.")

    monkeypatch.setattr(rotowire, "fetch_news", broken)

    with pytest.raises(CommandError, match="daily limit"):
        call_command("import_rotowire_news")


def test_the_command_refuses_while_switched_off(in_season, api_key, settings, monkeypatch):
    settings.ROTOWIRE_ENABLED = False
    monkeypatch.setattr(rotowire, "urlopen", pytest.fail)

    with pytest.raises(CommandError, match="ROTOWIRE_ENABLED is off"):
        call_command("import_rotowire_news")


def test_the_refusal_names_force_where_it_helps(db, api_key, monkeypatch):
    monkeypatch.setattr(rotowire, "urlopen", pytest.fail)

    with pytest.raises(CommandError, match="Run with --force"):
        call_command("import_rotowire_news")


def test_the_refusal_does_not_name_force_where_it_cannot_help(in_season, settings):
    settings.RAPID_API_KEY = ""

    with pytest.raises(CommandError) as refused:
        call_command("import_rotowire_news")

    assert "--force" not in str(refused.value)


def test_force_pulls_outside_the_window_and_below_the_reserve(db, api_key, payload, monkeypatch):
    quota(3)
    monkeypatch.setattr(rotowire, "fetch_news", lambda: payload)
    out = io.StringIO()

    call_command("import_rotowire_news", force=True, stdout=out)

    assert "RotoWire news imported: 25 items" in out.getvalue()


def test_force_does_not_override_the_switch(db, api_key, settings, monkeypatch):
    settings.ROTOWIRE_ENABLED = False
    monkeypatch.setattr(rotowire, "urlopen", pytest.fail)

    with pytest.raises(CommandError, match="ROTOWIRE_ENABLED is off"):
        call_command("import_rotowire_news", force=True)


def test_force_does_not_override_the_daily_limit(db, api_key, settings, monkeypatch):
    settings.ROTOWIRE_DAILY_LIMIT = 0
    monkeypatch.setattr(rotowire, "urlopen", pytest.fail)

    with pytest.raises(CommandError, match="daily limit"):
        call_command("import_rotowire_news", force=True)


def test_check_makes_no_request_and_reports(in_season, api_key, monkeypatch):
    quota(484)
    monkeypatch.setattr(rotowire, "urlopen", pytest.fail)
    out = io.StringIO()

    call_command("import_rotowire_news", check=True, stdout=out)

    assert "A pull would be made." in out.getvalue()
    assert "RapidAPI: 484 of 500 requests left, resets " in out.getvalue()
    assert not NbaApiUsage.objects.exists()


def test_check_says_why_not(db, api_key, monkeypatch):
    monkeypatch.setattr(rotowire, "urlopen", pytest.fail)
    out = io.StringIO()

    call_command("import_rotowire_news", check=True, stdout=out)

    assert "No pull would be made: outside the season window" in out.getvalue()
    assert "RapidAPI has not reported a count yet." in out.getvalue()


def test_the_checks_come_in_order(in_season, settings):
    quota(3)
    settings.RAPID_API_KEY = ""
    settings.ROTOWIRE_ENABLED = False
    assert rotowire.why_not_now() == "ROTOWIRE_ENABLED is off"

    settings.ROTOWIRE_ENABLED = True
    assert rotowire.why_not_now() == "RAPID_API_KEY is not set"

    settings.RAPID_API_KEY = "test-key"
    in_season.delete()
    assert rotowire.why_not_now().startswith("outside the season window")

    today = timezone.localdate()
    make_season("again", today, today + dt.timedelta(days=1))
    assert rotowire.why_not_now().startswith("only 3 RapidAPI requests left")
