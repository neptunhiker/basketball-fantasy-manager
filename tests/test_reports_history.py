"""The game log read off the daily snapshots: `apps.reports.history`."""

from decimal import Decimal

import pytest

from apps.reports.history import GameLog
from tests.reports_factories import at, make_player, make_season, make_team, snapshot

pytestmark = pytest.mark.django_db


@pytest.fixture
def season():
    return make_season()


def days(season, player, *rows, team=None):
    """Snapshots on November `day` at 12:15 with (day, total, games)."""
    for day, total, games in rows:
        made = snapshot(season, player, at(11, day, 12, 15), total=total, games=games)
        if team is not None:
            made.team = team
            made.save(update_fields=["team"])


def test_each_day_with_a_game_is_one_entry(season):
    who = make_player()
    days(season, who, (1, 0, 0), (2, 20, 1), (3, 20, 1), (4, 55, 2))

    log = GameLog(season, [who.pk])

    assert [(entry.at.day, entry.games, entry.fp) for entry in log.entries(who.pk)] == [
        (2, 1, Decimal(20)),
        (4, 1, Decimal(35)),
    ]


def test_the_first_snapshot_is_the_baseline_not_a_game(season):
    # Arrived mid-season with 10 games played elsewhere.
    who = make_player()
    days(season, who, (5, 300, 10), (6, 330, 11))

    assert GameLog(season, [who.pk]).window(who.pk).games == 1


def test_a_missed_day_merges_two_games(season):
    who = make_player()
    days(season, who, (1, 0, 0), (3, 50, 2))

    form = GameLog(season, [who.pk]).window(who.pk)

    assert (form.fp, form.games, form.per_game) == (Decimal(50), 2, Decimal(25))


def test_corrections_are_left_out_but_a_negative_game_counts(season):
    who = make_player()
    days(season, who, (1, 0, 0), (2, 20, 1), (3, 18, 1), (4, 16, 2), (5, 10, 1))

    entries = GameLog(season, [who.pk]).entries(who.pk)

    # Day 3: points changed without a game. Day 5: fewer games than before.
    assert [(entry.at.day, entry.fp) for entry in entries] == [(2, 20), (4, -2)]


def test_weekly_form_is_oldest_week_first(season):
    who = make_player()
    days(season, who, (1, 0, 0), (2, 10, 1), (9, 40, 2), (14, 100, 4), (15, 130, 5))

    weeks = GameLog(season, [who.pk]).weekly(who.pk, at(11, 15, 12, 15))

    # Weeks end Nov 1, 8 and 15 at 12:15; the Nov 1 snapshot is the baseline.
    assert [(form.games, form.fp) for form in weeks] == [(0, 0), (1, 10), (4, 120)]
    assert weeks[2].per_game == Decimal(30)


def test_a_week_without_games_has_no_average(season):
    who = make_player()
    days(season, who, (1, 0, 0), (2, 10, 1))

    weeks = GameLog(season, [who.pk]).weekly(who.pk, at(11, 15, 12, 15))

    assert [form.games for form in weeks] == [0, 1, 0]
    assert weeks[2].per_game is None


def test_a_window_can_keep_one_teams_games(season):
    old, new = make_team(), make_team()
    who = make_player(team=old)
    days(season, who, (1, 0, 0), (2, 10, 1), team=old)
    days(season, who, (3, 40, 2), team=new)

    log = GameLog(season, [who.pk])

    assert log.window(who.pk, team_id=new.pk).fp == 30
    assert log.window(who.pk, team_id=old.pk).fp == 10


def test_until_ignores_later_snapshots(season):
    who = make_player()
    days(season, who, (1, 0, 0), (2, 10, 1), (3, 40, 2))

    assert GameLog(season, [who.pk], until=at(11, 2, 12, 15)).window(who.pk).games == 1


def test_one_query_for_many_players(season, django_assert_num_queries):
    players = [make_player() for _ in range(5)]
    for who in players:
        days(season, who, (1, 0, 0), (2, 10, 1))

    with django_assert_num_queries(1):
        log = GameLog(season, [who.pk for who in players])

    assert all(log.window(who.pk).games == 1 for who in players)
