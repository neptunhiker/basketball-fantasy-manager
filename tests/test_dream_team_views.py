"""The season analytics page: The Dream Team, its changes, and its empty states."""

import datetime as dt
import re
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.analytics.dream_team import refresh
from apps.fantasy.models import PlayerSnapshot, Season
from apps.nba import charts
from apps.nba.models import Player, Team

pytestmark = pytest.mark.django_db

BERLIN = ZoneInfo("Europe/Berlin")
PRESEASON = dt.datetime(2026, 10, 13, 20, 0, tzinfo=BERLIN)
FIRST_IMPORT = dt.datetime(2026, 10, 24, 12, 15, tzinfo=BERLIN)
SECOND_IMPORT = dt.datetime(2026, 10, 26, 12, 15, tzinfo=BERLIN)
URL = "/analytics/"


@pytest.fixture
def signed_in(client, user, password):
    client.login(email=user.email, password=password)
    return client


@pytest.fixture
def season(db):
    return Season.objects.create(
        label="2026-27",
        starts_on=dt.date(2026, 10, 21),
        ends_on=dt.date(2027, 4, 12),
        is_current=True,
        starting_cash=Decimal(63_000_000),
    )


def snap(player, season, as_of, fp=0, games=0):
    PlayerSnapshot.objects.create(
        player=player,
        season=season,
        as_of=as_of,
        salary=Decimal(4_000_000),
        total_fp=Decimal(fp),
        games_played=games,
        position=player.position,
        team=player.team,
    )


@pytest.fixture
def league(season):
    """7 Guards, 7 Forwards, 3 Centers at $4M; Forwards 5 points ahead of the
    Guard with the same number, Centers 100, so no two players ever tie. In
    the first import G1 is the best Guard by far; by the second he has fallen
    to the worst. So the team swaps G1 out and G2 in, and nothing else."""
    team = Team.objects.get(abbreviation="LAL")
    players = {}
    for position, first in (("G", "Gina"), ("F", "Finn"), ("C", "Cleo")):
        for number in range(1, (3 if position == "C" else 7) + 1):
            p = Player.objects.create(
                first_name=first, last_name=f"{position}{number}", position=position, team=team
            )
            players[p.last_name] = p
            bonus = {"G": 0, "F": 5, "C": 100}[position]
            first_fp = 500 if p.last_name == "G1" else bonus + number * 10
            second_fp = bonus + number * 10 + 10
            snap(p, season, PRESEASON)
            snap(p, season, FIRST_IMPORT, first_fp, 2)
            snap(p, season, SECOND_IMPORT, second_fp, 3)
    return players


# --- access and navigation ----------------------------------------------------------


def test_the_page_needs_a_login(client):
    response = client.get(URL)
    assert response.status_code == 302
    assert "login" in response.url


def test_the_url_is_named(db):
    assert reverse("analytics:dream-team") == URL


def test_the_navigation_leads_to_it(signed_in, season):
    response = signed_in.get(URL)
    assert re.search(r'<a href="/analytics/"\s+class="[^"]*bg-primary-50', response.text)


# --- empty states -------------------------------------------------------------------


def test_without_a_current_season(signed_in, db):
    response = signed_in.get(URL)
    assert response.status_code == 200
    assert "No season is marked as current" in response.text


def test_before_any_stats(signed_in, season):
    response = signed_in.get(URL)
    assert "No stats have been imported for this season yet" in response.text


def test_without_draft_day_prices(signed_in, season):
    p = Player.objects.create(last_name="Late", position="G")
    snap(p, season, FIRST_IMPORT, 10, 1)

    response = signed_in.get(URL)
    assert "There are no prices from before the season started" in response.text


def test_before_the_first_game(signed_in, season):
    p = Player.objects.create(last_name="Early", position="G")
    snap(p, season, PRESEASON)

    response = signed_in.get(URL)
    assert "Nobody has played a game yet" in response.text


def test_not_calculated_yet_tells_staff_how(client, staff_user, password, league):
    client.login(email=staff_user.email, password=password)

    response = client.get(URL)
    assert "has not been worked out yet" in response.text
    assert "refresh_dream_team" in response.text


def test_not_calculated_yet_keeps_the_command_from_everyone_else(signed_in, league):
    response = signed_in.get(URL)
    assert "has not been worked out yet" in response.text
    assert "refresh_dream_team" not in response.text


# --- the Dream Team -------------------------------------------------------------------


def test_the_first_result_shows_the_team_and_says_it_is_the_first(signed_in, league, season):
    refresh(season, FIRST_IMPORT)

    response = signed_in.get(URL)
    text = response.text
    assert "The Dream Team" in text
    assert "This is the first Dream Team of the season" in text
    # All fifteen by name, linked to their pages.
    for name in [f"G{n}" for n in (1, 3, 4, 5, 6, 7)] + [f"F{n}" for n in range(2, 8)]:
        assert reverse("nba:player-detail", args=[league[name].slug]) in text
    assert ">New<" not in text
    # 15 x $4M of $63M.
    assert "$60.00M" in text and "$3.00M" in text


def test_the_newest_result_shows_who_came_in_and_who_went_out(signed_in, league, season):
    refresh(season, FIRST_IMPORT)
    refresh(season, SECOND_IMPORT)

    response = signed_in.get(URL)
    changes = response.context["changes"]
    assert [pick.player.last_name for pick in changes.came_in] == ["G2"]
    assert [pick.player.last_name for pick in changes.went_out] == ["G1"]
    text = response.text
    assert "Compared with" in text
    assert "In · 1" in text and "Out · 1" in text
    assert "14<span" in text  # stayed
    assert text.count(">New<") == 1


def test_an_unchanged_team_says_so(signed_in, league, season):
    refresh(season, SECOND_IMPORT - dt.timedelta(minutes=1))  # = the first import
    PlayerSnapshot.objects.filter(player=league["G1"], as_of=FIRST_IMPORT).update(total_fp=5)
    refresh(season, FIRST_IMPORT)
    refresh(season, SECOND_IMPORT)

    response = signed_in.get(URL)
    assert "Unchanged: the same 15 as last time." in response.text


def test_the_chart_and_the_players_left_out(signed_in, league, season):
    refresh(season, SECOND_IMPORT)

    response = signed_in.get(URL)
    context = response.context
    # 17 players have played: both drawings show all of them.
    assert len(context["chart"]["dots"]) == 17
    assert len(context["compact_chart"]["dots"]) == 17
    assert context["chart"]["width"] > context["compact_chart"]["width"]
    # The picks are drawn last, over everyone else.
    assert [dot["picked"] for dot in context["chart"]["dots"]][-15:] == [True] * 15
    # F1 (25) and G1 (20) are out; the weakest pick, G2, has 30. Nobody left
    # out outscored a pick, so nobody is listed.
    assert context["left_out"] == []
    assert "no player outside the team has outscored the weakest pick" in response.text


def test_players_who_outscored_a_pick_are_listed_as_left_out(signed_in, league, season):
    """A star too expensive to fit: more points than any pick, but $40M."""
    star = Player.objects.create(
        first_name="Sam", last_name="Star", position="G", team=Team.objects.get(abbreviation="LAL")
    )
    PlayerSnapshot.objects.create(
        player=star,
        season=season,
        as_of=PRESEASON,
        salary=Decimal(40_000_000),
        total_fp=0,
        games_played=0,
        position="G",
        team=star.team,
    )
    snap(star, season, SECOND_IMPORT, 900, 3)
    refresh(season, SECOND_IMPORT)

    response = signed_in.get(URL)
    assert [entry["player"].last_name for entry in response.context["left_out"]] == ["Star"]


def test_the_page_costs_the_same_queries_however_long_the_history(signed_in, league, season):
    refresh(season, FIRST_IMPORT)
    refresh(season, SECOND_IMPORT)
    signed_in.get(URL)  # warm up session and language lookups

    with CaptureQueriesContext(connection) as two:
        signed_in.get(URL)

    for day in range(27, 32):
        moment = dt.datetime(2026, 10, day, 12, tzinfo=BERLIN)
        for p in league.values():
            snap(p, season, moment, 1, 1)
        refresh(season, moment)

    with CaptureQueriesContext(connection) as many:
        signed_in.get(URL)

    assert len(many) == len(two)


def test_the_name_stays_english_in_german(signed_in, user, league, season):
    refresh(season, SECOND_IMPORT)
    user.language = "de"
    user.save()

    response = signed_in.get(URL)
    assert "The Dream Team" in response.text


# --- the chart's drawing size --------------------------------------------------------


def test_a_scatter_chart_can_be_drawn_at_another_size():
    points = [("A", Decimal(1), Decimal(10)), ("B", Decimal(5), Decimal(50))]

    default = charts.scatter_chart(points)
    narrow = charts.scatter_chart(points, width=360, height=300)

    assert default["width"] == charts.WIDTH
    assert narrow["width"] == 360
    assert narrow["plot_right"] == 360 - charts.PAD_RIGHT
    assert max(dot["x"] for dot in narrow["dots"]) <= narrow["plot_right"]
    assert narrow["plot_bottom"] > default["plot_bottom"]
