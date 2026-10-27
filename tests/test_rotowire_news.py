"""The RotoWire news import: parsing the feed, matching players, never storing twice.

The fixture is a real pull of the NBA feed (25 items from October 27, 2026).
"""

import copy
import datetime as dt
import io
import json
from datetime import date
from pathlib import Path

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone
from django_q.models import Schedule

from apps.nba import rotowire, tasks
from apps.nba.models import NbaApiUsage, Player, PlayerNews, Team
from apps.nba.rotowire import (
    PROVIDER,
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


class FakeResponse(io.BytesIO):
    status = 200

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


def test_fetch_without_a_key_makes_no_request(db, settings):
    settings.RAPID_API_KEY = ""

    with pytest.raises(NbaApiError):
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


# -- schedule, task and command --------------------------------------------------


def test_the_schedule_exists_after_migrate(db):
    schedule = Schedule.objects.get(func="apps.nba.tasks.scheduled_rotowire_news_import")

    assert schedule.schedule_type == Schedule.CRON
    assert schedule.cron == "0 */4 * * *"
    assert schedule.repeats == -1
    assert not schedule.args
    assert not schedule.kwargs


def test_the_first_run_waits_for_the_next_four_hour_mark():
    from importlib import import_module

    migration = import_module("apps.nba.migrations.0019_scheduled_rotowire_news_import")

    next_run = migration.next_four_hour_mark()

    assert next_run.hour % 4 == 0
    assert (next_run.minute, next_run.second) == (0, 0)
    assert dt.timedelta(0) < next_run - timezone.now() <= dt.timedelta(hours=4)


def test_without_a_key_the_task_does_nothing(db, settings, monkeypatch):
    settings.RAPID_API_KEY = ""
    monkeypatch.setattr(tasks, "sync_news", pytest.fail)

    assert scheduled_rotowire_news_import().startswith("skipped")


def test_the_task_imports_and_reports(db, api_key, payload, monkeypatch):
    monkeypatch.setattr(rotowire, "fetch_news", lambda: payload)

    result = scheduled_rotowire_news_import()

    assert result.startswith("25 items, 25 new")
    assert PlayerNews.objects.count() == 25


def test_the_task_warns_when_every_item_was_new(db, api_key, payload, monkeypatch, caplog):
    older = copy.deepcopy(payload)
    older["items"] = [payload["items"][0]]
    sync_news(older)
    newer = copy.deepcopy(payload)
    newer["items"] = payload["items"][1:]
    monkeypatch.setattr(rotowire, "fetch_news", lambda: newer)

    scheduled_rotowire_news_import()

    assert "some may have been missed" in caplog.text


def test_the_task_does_not_warn_on_an_overlapping_pull(db, api_key, payload, monkeypatch, caplog):
    sync_news(payload)
    monkeypatch.setattr(rotowire, "fetch_news", lambda: payload)

    scheduled_rotowire_news_import()

    assert "some may have been missed" not in caplog.text


def test_a_failed_request_fails_the_task(db, api_key, monkeypatch):
    def broken():
        raise NbaApiError("RotoWire API returned HTTP 503.")

    monkeypatch.setattr(rotowire, "fetch_news", broken)

    with pytest.raises(NbaApiError):
        scheduled_rotowire_news_import()


def test_the_command_imports_and_reports(db, payload, monkeypatch):
    monkeypatch.setattr(rotowire, "fetch_news", lambda: payload)
    out = io.StringIO()

    call_command("import_rotowire_news", stdout=out)

    assert "RotoWire news imported: 25 items, 25 new" in out.getvalue()


def test_the_command_turns_an_api_error_into_a_command_error(db, monkeypatch):
    def broken():
        raise DailyApiLimitExceeded("The rotowire daily limit of 8 requests has been reached.")

    monkeypatch.setattr(rotowire, "fetch_news", broken)

    with pytest.raises(CommandError, match="daily limit"):
        call_command("import_rotowire_news")
