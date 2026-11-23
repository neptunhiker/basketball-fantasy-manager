"""Analytics -> Insights, and the tab bar it shares with the Dream Team."""

import datetime as dt
import re
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.analytics.models import DreamTeam
from apps.fantasy.models import PlayerSnapshot, Roster, RosterPlayer, Season
from apps.nba.models import Player

BERLIN = ZoneInfo("Europe/Berlin")
URL = "/analytics/insights/"
DREAM_URL = "/analytics/"


def at(month, day, hour=12):
    return dt.datetime(2026, month, day, hour, tzinfo=BERLIN)


DAY1, DAY2 = at(10, 21, 20), at(10, 22, 20)


@pytest.fixture
def season(db):
    return Season.objects.create(
        label="2026-27",
        starts_on=dt.date(2026, 10, 21),
        ends_on=dt.date(2027, 4, 11),
        is_current=True,
        signings_close_at=at(10, 20, 23),
        starting_cash=Decimal("60000000"),
    )


@pytest.fixture
def signed_in(client, user, password):
    client.login(email=user.email, password=password)
    return client


_names = iter(f"Player{i:02d}" for i in range(1000))


def roster_with(season, manager, name, totals, **extra):
    roster = Roster.objects.create(manager=manager, season=season, name=name, **extra)
    who = Player.objects.create(first_name="Ann", last_name=next(_names), position="G")
    RosterPlayer.objects.create(roster=roster, player=who, added_at=at(10, 10))
    for moment, total in zip((DAY1, DAY2), totals, strict=False):
        PlayerSnapshot.objects.create(
            player=who,
            season=season,
            as_of=moment,
            salary=Decimal("3000000"),
            total_fp=Decimal(total),
            games_played=1,
            position="G",
        )
    return roster


def dream_team(season, moment, total):
    DreamTeam.objects.create(
        season=season,
        as_of=moment,
        draft_as_of=at(10, 1),
        budget=Decimal("60000000"),
        total_salary=Decimal("59000000"),
        total_fp=Decimal(total),
        candidates=300,
    )


@pytest.fixture
def raced(season, manager, other_manager):
    mine = roster_with(season, manager, "Koalas", (300, 650))
    theirs = roster_with(season, other_manager, "Rivals", (400, 1250))
    roster_with(season, manager, "What if", (900, 2000), is_active=False)
    dream_team(season, DAY1, 500)
    dream_team(season, DAY2, 1000)
    return {"mine": mine, "theirs": theirs}


# --- the tabs ----------------------------------------------------------------


def test_the_urls():
    assert reverse("analytics:dream-team") == DREAM_URL
    assert reverse("analytics:insights") == URL


def test_signed_out_users_are_sent_to_log_in(client, db):
    response = client.get(URL)

    assert response.status_code == 302
    assert "login" in response.url


@pytest.mark.parametrize(("url", "current"), [(DREAM_URL, DREAM_URL), (URL, URL)])
def test_the_open_tab_is_marked_current(signed_in, season, url, current):
    response = signed_in.get(url)

    nav = re.search(r'<nav aria-label="Analytics sections".*?</nav>', response.text, re.S)[0]
    assert re.search(rf'href="{current}"\s+aria-current="page"', nav)
    assert nav.count('aria-current="page"') == 1


def test_the_sidebar_keeps_analytics_highlighted_on_insights(signed_in, season):
    response = signed_in.get(URL)

    assert re.search(r'<a href="/analytics/"\s+class="[^"]*bg-primary-50', response.text)


@pytest.mark.parametrize("url", [DREAM_URL, URL])
def test_without_a_current_season_both_tabs_say_so(signed_in, db, url):
    response = signed_in.get(url)

    assert response.status_code == 200
    assert "No season is marked as current" in response.text
    assert 'aria-label="Analytics sections"' in response.text


# --- Insights ----------------------------------------------------------------


def test_before_the_first_import_the_race_has_not_started(signed_in, season):
    response = signed_in.get(URL)

    assert "The race has not started yet" in response.text
    assert response.context["race"] is None


def test_the_standings_rank_active_rosters_against_the_dream_team(signed_in, raced):
    response = signed_in.get(URL)
    text = response.text

    rows = [(row["rank"], row["line"].name, row["gap"]) for row in response.context["standings"]]
    assert rows == [(1, "Rivals", -250), (2, "Dream Team", None), (3, "Koalas", 350)]
    assert "What if" not in text
    assert "250 ahead" in text
    assert "350 behind" in text
    # What the last import added: 650 - 300 and 1250 - 400.
    assert "+350" in text
    assert "+850" in text


def test_the_viewers_own_roster_is_marked_and_summed_up(signed_in, raced):
    response = signed_in.get(URL)

    assert response.context["own_best"]["line"].name == "Koalas"
    assert "Place 3 of 3" in response.text
    assert response.text.count(">You<") == 1
    assert response.context["leader"]["line"].name == "Rivals"


def test_others_rosters_are_shown_but_not_linked(signed_in, raced):
    response = signed_in.get(URL)

    assert "Rivals" in response.text
    assert reverse("fantasy:roster-build", args=[raced["theirs"].pk]) not in response.text


def test_without_active_rosters_the_dream_team_runs_alone(signed_in, season):
    who = Player.objects.create(first_name="Ann", last_name="Solo", position="G")
    PlayerSnapshot.objects.create(
        player=who,
        season=season,
        as_of=DAY1,
        salary=Decimal("3000000"),
        total_fp=Decimal(10),
        games_played=1,
        position="G",
    )
    dream_team(season, DAY1, 500)

    response = signed_in.get(URL)

    assert "No roster is in the race yet." in response.text
    assert "Put a roster in the race" in response.text
    assert [row["line"].name for row in response.context["standings"]] == ["Dream Team"]


def test_more_rosters_cost_no_more_queries(signed_in, raced, season, manager):
    def count():
        with CaptureQueriesContext(connection) as queries:
            assert signed_in.get(URL).status_code == 200
        return len(queries)

    before = count()
    for index in range(4):
        roster_with(season, manager, f"More {index}", (10, 20))

    assert count() == before


def test_the_chart_is_drawn_with_both_drawings_and_its_readout(signed_in, raced):
    response = signed_in.get(URL)

    for name in ("wide", "medium", "compact"):
        assert f'id="points-race-{name}"' in response.text
    assert 'id="points-race-readout"' in response.text
    assert "js/points_race.js" in response.text
    assert response.context["race_readout"]["days"][-1] == "22 Oct"


def test_in_german_the_coordinates_stay_numbers_the_browser_can_draw(signed_in, raced, user):
    user.language = "de"
    user.save()

    response = signed_in.get(URL)

    assert "Punkterennen" in response.text
    coordinates = re.findall(r'\b(?:x|y|x1|y1|x2|y2|cx|cy)="([^"]*)"', response.text)
    assert coordinates
    assert not [value for value in coordinates if "," in value]
    # The figures themselves are German: 1.250 rather than 1,250.
    assert "1.250" in response.text


# --- team value ------------------------------------------------------------------------


def raise_salary(roster, millions):
    """Reprice the roster's player at the second import."""
    PlayerSnapshot.objects.filter(player__roster_memberships__roster=roster, as_of=DAY2).update(
        salary=Decimal(millions) * 1_000_000
    )


def test_the_team_value_chart_is_drawn_like_the_points_race(signed_in, raced):
    response = signed_in.get(URL)
    text = response.text

    for name in ("wide", "medium", "compact"):
        assert f'id="value-race-{name}"' in text
    assert 'id="value-race-readout"' in text
    assert 'id="value-race-heading"' in text
    # One script for both cards, which it links.
    assert text.count("js/points_race.js") == 1
    assert ">Budget<" in text
    assert response.context["value_readout"]["days"] == response.context["race_readout"]["days"]


def test_the_standings_show_each_rosters_team_value_and_its_change(signed_in, raced):
    # $60M cash and a $3M player: $63M at the start, $64.50M once he is $4.5M.
    raise_salary(raced["mine"], "4.5")

    response = signed_in.get(URL)
    text = response.text

    assert ">Team value</th>" in text
    assert "$64.50M" in text
    assert "+$1.50M" in text
    assert "text-emerald-700" in text
    # On a phone the value leads the roster's second line.
    assert re.search(r"\$64\.50M · </span>Bulla", text)


def test_a_loss_of_value_is_rose_and_no_change_is_grey(signed_in, raced):
    raise_salary(raced["theirs"], "2")

    text = signed_in.get(URL).text

    assert "-$1.00M" in text and "text-rose-700" in text
    # The Dream Team has no stored picks here: no value, a dash, no change line.
    row = re.search(r"Best roster from draft day.*?</tr>", text, re.S)[0]
    assert "\u2013" in row  # the en dash `millions` writes for no amount
    assert "Since signings closed" not in row


def test_in_german_the_values_are_german_too(signed_in, raced, user):
    raise_salary(raced["mine"], "4.5")
    user.language = "de"
    user.save()

    text = signed_in.get(URL).text

    assert "$64,50M" in text
    coordinates = re.findall(r'\b(?:x|y|x1|y1|x2|y2|cx|cy)="([^"]*)"', text)
    assert not [value for value in coordinates if "," in value]
