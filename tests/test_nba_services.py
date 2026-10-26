from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from django.conf import settings
from django.test import override_settings
from django.utils import timezone

from apps.nba.models import NbaApiUsage, Player, PlayerInjury, Team
from apps.nba.services import (
    RECOVERED_STATUS,
    DailyApiLimitExceeded,
    _reserve_api_call,
    daily_limit_for,
    parse_injuries,
    sync_injuries,
)

FIRST_SYNC = datetime(2026, 9, 12, 9, tzinfo=UTC)
SECOND_SYNC = datetime(2026, 9, 13, 9, tzinfo=UTC)


@pytest.fixture
def injury_payload():
    return {
        "status": "success",
        "response": [
            {
                "team": {"id": 13, "name": "Los Angeles Lakers", "abbreviation": "LAL"},
                "injuries": [
                    {
                        "id": "inj-1",
                        "status": "Out",
                        "date": "2026-09-12T08:00:00Z",
                        "details": {
                            "fantasyStatus": {"description": "GTD", "abbreviation": "GTD"},
                            "returnDate": "2026-09-20",
                            "type": {"name": "Foot"},
                            "location": "Leg",
                            "detail": "Fracture",
                            "side": "Left",
                        },
                        "type": {"name": "Knee"},
                        "source": {"id": "1", "state": "basic", "description": "basic/manual"},
                        "athlete": {
                            "firstName": "Luka",
                            "lastName": "Dončić",
                            "position": {"displayName": "Guard", "abbreviation": "G"},
                            "status": {"name": "Active"},
                            "team": {"id": 13, "name": "Los Angeles Lakers", "abbreviation": "LAL"},
                            "notes": {
                                "items": [{"headline": "Foot soreness", "source": "Provider"}]
                            },
                        },
                        "shortComment": "Soreness",
                    }
                ],
            }
        ],
    }


def test_parse_injuries_flattens_nested_provider_fields(injury_payload):
    injury = parse_injuries(injury_payload)[0]

    assert injury.provider_injury_id == "inj-1"
    assert injury.team_abbreviation == "LAL"
    assert injury.fantasy_status == "GTD"
    assert injury.fantasy_status_abbreviation == "GTD"
    assert injury.reported_at == datetime(2026, 9, 12, 8, tzinfo=UTC)
    assert injury.injury_type == "Foot"
    assert injury.injury_location == "Leg"
    assert injury.injury_detail == "Fracture"
    assert injury.injury_side == "Left"
    assert injury.provider_source_description == "basic/manual"
    assert injury.injury_status_type == "Knee"
    assert injury.headline == "Foot soreness"
    assert injury.feed_player_position_abbreviation == "G"
    assert injury.raw_payload["id"] == "inj-1"


def test_parse_injuries_accepts_current_top_level_injuries_envelope(injury_payload):
    current_payload = {"status": "success", "injuries": injury_payload["response"]}

    injuries = parse_injuries(current_payload)

    assert len(injuries) == 1
    assert injuries[0].feed_player_name == "Luka Dončić"


def test_sync_injuries_matches_name_and_team_and_keeps_history(db, injury_payload):
    team = Team.objects.get(abbreviation="LAL")
    player = Player.objects.create(
        first_name="Luka", last_name="Doncic", position=Player.Position.GUARD, team=team
    )
    first_observation = timezone.make_aware(datetime(2026, 9, 12, 9))
    second_observation = timezone.make_aware(datetime(2026, 9, 13, 9))

    assert sync_injuries(injury_payload, first_observation) == {
        "created": 1,
        "updated": 0,
        "recovered": 0,
        "unmatched": 0,
        "ambiguous": 0,
    }
    assert sync_injuries(injury_payload, second_observation)["created"] == 1
    assert PlayerInjury.objects.filter(player=player).count() == 2


@override_settings(RAPID_API_DAILY_LIMIT=1)
def test_api_usage_enforces_the_configured_daily_limit(db):
    now = timezone.make_aware(datetime(2026, 9, 12, 9))

    _reserve_api_call(now)
    with pytest.raises(DailyApiLimitExceeded):
        _reserve_api_call(now)

    usage = NbaApiUsage.objects.get(usage_date=now.date())
    assert usage.request_count == 1


def test_a_superuser_may_call_five_times_a_day(db):
    now = timezone.make_aware(datetime(2026, 9, 12, 9))
    limit = daily_limit_for(SimpleNamespace(is_superuser=True))

    for _ in range(5):
        _reserve_api_call(now, daily_limit=limit)
    with pytest.raises(DailyApiLimitExceeded):
        _reserve_api_call(now, daily_limit=limit)


def test_the_tally_is_shared_between_users_and_superusers(db):
    """After anyone's call, only a superuser can call again that day."""
    now = timezone.make_aware(datetime(2026, 9, 12, 9))
    user_limit = daily_limit_for(SimpleNamespace(is_superuser=False))
    superuser_limit = daily_limit_for(SimpleNamespace(is_superuser=True))

    _reserve_api_call(now, daily_limit=superuser_limit)
    with pytest.raises(DailyApiLimitExceeded):
        _reserve_api_call(now, daily_limit=user_limit)
    _reserve_api_call(now, daily_limit=superuser_limit)

    assert NbaApiUsage.objects.get(usage_date=now.date()).request_count == 2


def test_the_management_command_gets_the_superuser_limit():
    assert daily_limit_for(None) == settings.RAPID_API_SUPERUSER_DAILY_LIMIT == 5


@override_settings(RAPID_API_SUPERUSER_DAILY_LIMIT=2)
def test_no_caller_may_go_past_the_superuser_limit(db):
    now = timezone.make_aware(datetime(2026, 9, 12, 9))

    _reserve_api_call(now, daily_limit=10)
    _reserve_api_call(now, daily_limit=10)
    with pytest.raises(DailyApiLimitExceeded):
        _reserve_api_call(now, daily_limit=10)


@pytest.mark.parametrize(
    ("feed_code", "our_code"),
    [("GS", "GSW"), ("NY", "NYK"), ("NO", "NOP"), ("SA", "SAS"), ("UTAH", "UTA"), ("WSH", "WAS")],
)
def test_sync_matches_teams_the_feed_writes_with_other_codes(
    db, injury_payload, feed_code, our_code
):
    player = Player.objects.create(
        first_name="Luka",
        last_name="Doncic",
        position=Player.Position.GUARD,
        team=Team.objects.get(abbreviation=our_code),
    )
    group = injury_payload["response"][0]
    group["team"]["abbreviation"] = feed_code
    group["injuries"][0]["athlete"]["team"]["abbreviation"] = feed_code

    summary = sync_injuries(injury_payload, FIRST_SYNC)

    assert summary["created"] == 1
    assert summary["unmatched"] == 0
    assert PlayerInjury.objects.get(player=player).feed_team_abbreviation == feed_code


def _injured_player(first_name, last_name, team_abbreviation="BOS"):
    player = Player.objects.create(
        first_name=first_name,
        last_name=last_name,
        position=Player.Position.FORWARD,
        team=Team.objects.get(abbreviation=team_abbreviation),
    )
    PlayerInjury.objects.create(player=player, observed_at=FIRST_SYNC, status="Out")
    return player


def test_sync_marks_a_player_who_dropped_out_of_the_feed_as_healthy(db, injury_payload):
    player = _injured_player("Jayson", "Tatum")

    summary = sync_injuries(injury_payload, SECOND_SYNC)

    assert summary["recovered"] == 1
    player = Player.objects.get(pk=player.pk)
    assert player.latest_injury.status == RECOVERED_STATUS
    assert player.latest_injury.observed_at == SECOND_SYNC
    assert not player.is_injured


def test_sync_keeps_a_player_injured_while_the_feed_still_lists_him(db, injury_payload):
    team = Team.objects.get(abbreviation="LAL")
    player = Player.objects.create(
        first_name="Luka", last_name="Doncic", position=Player.Position.GUARD, team=team
    )

    sync_injuries(injury_payload, FIRST_SYNC)
    summary = sync_injuries(injury_payload, SECOND_SYNC)

    assert summary["recovered"] == 0
    assert Player.objects.get(pk=player.pk).is_injured


def test_sync_keeps_an_unmatched_feed_player_injured(db, injury_payload):
    # Listed for LAL in the feed, but on another team locally, so the feed
    # entry cannot be matched: he is still in the report, not recovered.
    player = _injured_player("Luka", "Doncic", team_abbreviation="DAL")

    summary = sync_injuries(injury_payload, SECOND_SYNC)

    assert summary == {
        "created": 0,
        "updated": 0,
        "recovered": 0,
        "unmatched": 1,
        "ambiguous": 0,
    }
    assert Player.objects.get(pk=player.pk).is_injured


def test_sync_with_an_empty_feed_marks_nobody_healthy(db):
    player = _injured_player("Jayson", "Tatum")

    summary = sync_injuries({"status": "success", "response": []}, SECOND_SYNC)

    assert summary["recovered"] == 0
    assert Player.objects.get(pk=player.pk).is_injured


def test_sync_does_not_report_an_already_healthy_player_again(db, injury_payload):
    player = _injured_player("Jayson", "Tatum")

    sync_injuries(injury_payload, SECOND_SYNC)
    summary = sync_injuries(injury_payload, datetime(2026, 9, 14, 9, tzinfo=UTC))

    assert summary["recovered"] == 0
    assert PlayerInjury.objects.filter(player=player).count() == 2


def test_sync_leaves_players_without_any_injury_report_alone(db, injury_payload):
    player = Player.objects.create(
        first_name="Jayson",
        last_name="Tatum",
        position=Player.Position.FORWARD,
        team=Team.objects.get(abbreviation="BOS"),
    )

    sync_injuries(injury_payload, SECOND_SYNC)

    assert not PlayerInjury.objects.filter(player=player).exists()
