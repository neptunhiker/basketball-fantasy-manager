"""RapidAPI's monthly count, kept per provider, and the injury feed's reserve."""

import datetime as dt
import json

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.core.dates import local_date
from apps.nba import quota, services
from apps.nba.models import ESPN_TEAM_IDS, ApiQuota, NbaApiUsage, Team
from apps.nba.services import PROVIDER, MonthlyReserveReached, fetch_injuries

from .test_rotowire_news import FakeResponse, rapid_headers


@pytest.fixture
def api_key(settings):
    settings.RAPID_API_KEY = "test-key"


def left(remaining, provider=PROVIDER, resets_in=dt.timedelta(days=10)):
    now = timezone.now()
    return ApiQuota.objects.create(
        provider=provider,
        limit=90,
        remaining=remaining,
        resets_at=now + resets_in,
        reported_at=now,
    )


def test_each_provider_keeps_its_own_count(db):
    quota.record("rotowire", rapid_headers(484))
    quota.record(PROVIDER, rapid_headers(85, limit=90))

    assert ApiQuota.objects.get(provider="rotowire").remaining == 484
    assert ApiQuota.objects.get(provider=PROVIDER).remaining == 85


def test_a_low_count_only_stops_its_own_provider(db):
    left(3)

    assert quota.low(PROVIDER, 10) is not None
    assert quota.low("rotowire", 10) is None


def test_the_reason_names_the_count_and_the_reset(db):
    low = left(3)

    reason = quota.describe(low, 10)

    assert reason.startswith("only 3 RapidAPI requests left until ")
    assert reason.endswith("below the reserve of 10")


# -- the injury feed -------------------------------------------------------------


def test_an_injury_response_records_the_count(db, api_key, monkeypatch):
    body = json.dumps({"status": "success", "response": []}).encode()
    monkeypatch.setattr(
        services,
        "urlopen",
        lambda request, timeout: FakeResponse(body, rapid_headers(85, limit=90)),
    )

    fetch_injuries()

    recorded = ApiQuota.objects.get(provider=PROVIDER)
    assert (recorded.limit, recorded.remaining) == (90, 85)


def test_below_the_reserve_the_injury_feed_makes_no_request(db, api_key, monkeypatch):
    left(9)
    monkeypatch.setattr(services, "urlopen", pytest.fail)

    with pytest.raises(MonthlyReserveReached):
        fetch_injuries()

    assert not NbaApiUsage.objects.exists()


def test_at_the_reserve_the_injury_feed_still_calls(db, api_key, monkeypatch):
    left(10)
    body = json.dumps({"status": "success", "response": []}).encode()
    monkeypatch.setattr(services, "urlopen", lambda request, timeout: FakeResponse(body))

    fetch_injuries()

    assert NbaApiUsage.objects.get(provider=PROVIDER).request_count == 1


def test_the_injury_button_says_when_it_works_again(client, user, password, api_key, monkeypatch):
    low = left(4)
    monkeypatch.setattr(services, "urlopen", pytest.fail)
    client.login(email=user.email, password=password)

    response = client.post(reverse("nba:injury-refresh"), HTTP_HX_REQUEST="true")

    page = response.content.decode()
    assert "nearly used up" in page
    assert local_date(low.resets_at) in page


# -- the teams' ESPN IDs ---------------------------------------------------------


def test_every_seeded_team_has_its_espn_id(db):
    call_command("seed_teams", verbosity=0)

    assert dict(Team.objects.values_list("abbreviation", "espn_id")) == ESPN_TEAM_IDS
    assert len(set(ESPN_TEAM_IDS.values())) == 30


def test_a_team_is_found_by_its_abbreviation_as_natural_key(db):
    call_command("seed_teams", verbosity=0)

    assert Team.objects.get_by_natural_key("LAL").natural_key() == ("LAL",)
