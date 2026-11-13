"""Played, no game or DNP on the screens: the player page and the Latest FP column."""

import re

import pytest
from django.urls import reverse

from apps.fantasy.models import Season
from tests.test_game_status import day, game, injury, player, season, snap, teams  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def signed_in(client, user, password):
    client.login(email=user.email, password=password)
    return client


@pytest.fixture
def flagg(teams, season):  # noqa: F811
    """Played @ BOS, rested, sat out vs LAL hurt: one of each, newest last."""
    dal, bos, lal = teams["DAL"], teams["BOS"], teams["LAL"]
    who = player(dal)
    who.slug = "cooper-flagg"
    who.save()
    for n in range(4):  # the four games behind his first read
        game(dal, bos, day(n - 8, hour=1), season)
    snap(who, season, day(0), 4)
    game(bos, dal, day(1, hour=1), season)
    snap(who, season, day(1), 5)
    snap(who, season, day(2), 5)
    injury(who, day(2, hour=14), status="Out", location="Ankle")
    game(dal, lal, day(3, hour=1), season)
    snap(who, season, day(3), 5)
    return who


def page(client, who):
    return client.get(reverse("nba:player-detail", args=[who.slug])).text


def snapshot_rows(body):
    table = body[body.index('id="snapshots"') :]
    return re.findall(r"<tr class=\"group align-top.*?</tr>", table, flags=re.S)


def test_each_snapshot_row_says_whether_he_played(signed_in, flagg):
    rows = snapshot_rows(page(signed_in, flagg))

    dnp, rest, played, first = rows
    assert "DNP" in dnp and "vs" in dnp and "LAL" in dnp
    assert "Injured" in dnp and "(Ankle)" in dnp
    assert "No game" in rest and "data-quiet" in rest
    assert "@ BOS" in played and "data-quiet" not in played
    assert "No earlier snapshot this season" in first


def test_dnp_is_explained_for_people_and_screen_readers(signed_in, flagg):
    body = page(signed_in, flagg)

    assert "Did not play: his team played without him" in body
    assert "did not play, his team did" in body  # the legend under the table


def test_the_summary_counts_his_season(signed_in, flagg):
    body = page(signed_in, flagg)

    assert "Played 5 of 6 team games" in body
    assert "1 DNP" in body
    assert "1 ×</span> injured" in body  # noqa: RUF001


def test_no_summary_before_his_team_plays(signed_in, teams, season):  # noqa: F811
    who = player(teams["DAL"])
    snap(who, season, day(0), 0)

    body = page(signed_in, who)

    assert "team games" not in body


def test_season_dividers_only_across_seasons(signed_in, flagg, teams):  # noqa: F811
    assert 'scope="colgroup"' not in page(signed_in, flagg)

    old = Season.objects.create(
        label="2025-26", starts_on=day(-200).date(), ends_on=day(-60).date()
    )
    snap(flagg, old, day(-70), 70)

    body = page(signed_in, flagg)
    assert body.count('scope="colgroup"') == 2
    assert "2025-26" in body


def test_the_player_page_in_german(signed_in, flagg, user):
    user.language = "de"
    user.save()

    body = page(signed_in, flagg)

    assert "Kein Spiel" in body
    assert "6 Teamspielen" in body
    assert "Verletzt" in body


def test_the_chart_rings_the_game_he_sat_out(signed_in, flagg):
    body = page(signed_in, flagg)
    start = body.index('id="fp-per-game-fill"')
    chart = body[start : body.index("</svg>", start)]

    assert "text-amber-500" in chart  # the ring and the tick
    assert "DNP · vs LAL · Injured" in re.sub(r"\s+", " ", chart)
    assert "did not play, his team did" in body
