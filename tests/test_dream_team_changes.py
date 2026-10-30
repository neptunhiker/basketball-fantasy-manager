"""Who came into the Dream Team and who went out since the import before."""

import datetime as dt
from decimal import Decimal

import pytest

from apps.analytics.dream_team import changes, previous_of
from apps.analytics.models import DreamTeam, DreamTeamPick
from apps.fantasy.models import Season
from apps.nba.models import Player

pytestmark = pytest.mark.django_db

M = 1_000_000


def moment(day):
    return dt.datetime(2026, 10, day, 12, 15, tzinfo=dt.UTC)


@pytest.fixture
def season(db):
    return Season.objects.create(
        label="2026-27", starts_on=dt.date(2026, 10, 21), ends_on=dt.date(2027, 4, 12)
    )


@pytest.fixture
def players(db):
    return {name: Player.objects.create(last_name=name, position="G") for name in "ABCDEFG"}


def team(season, day, picks):
    """A stored result with `picks` as `(player, salary in $M, points)`."""
    dream_team = DreamTeam.objects.create(
        season=season,
        as_of=moment(day),
        draft_as_of=moment(13),
        budget=Decimal(63 * M),
        total_salary=sum(Decimal(salary * M) for _, salary, _ in picks),
        total_fp=sum(Decimal(points) for _, _, points in picks),
        candidates=100,
    )
    for player, salary, points in picks:
        DreamTeamPick.objects.create(
            dream_team=dream_team,
            player=player,
            position="G",
            salary=Decimal(salary * M),
            total_fp=Decimal(points),
            games_played=3,
        )
    return dream_team


def test_the_first_result_of_a_season_has_nothing_to_compare_with(season, players):
    current = team(season, 24, [(players["A"], 5, 50), (players["B"], 4, 40)])

    result = changes(current)

    assert result.is_first
    assert not result.unchanged
    assert result.stayed == 2
    assert result.salary_change is None


def test_an_identical_team_is_unchanged(season, players):
    picks = [(players["A"], 5, 50), (players["B"], 4, 40)]
    team(season, 24, picks)
    current = team(season, 26, picks)

    result = changes(current)

    assert result.unchanged
    assert result.came_in == [] and result.went_out == []
    assert result.stayed == 2
    assert result.salary_change == 0


def test_a_swap_shows_who_came_in_and_who_went_out(season, players):
    team(season, 24, [(players["A"], 5, 50), (players["B"], 4, 40), (players["C"], 3, 30)])
    current = team(
        season,
        26,
        [(players["A"], 5, 60), (players["D"], 2, 45), (players["E"], 1, 55)],
    )

    result = changes(current)

    assert [pick.player.last_name for pick in result.came_in] == ["E", "D"]
    assert [pick.player.last_name for pick in result.went_out] == ["B", "C"]
    assert result.stayed == 1
    # 8 now against 12 before.
    assert result.salary_change == Decimal(-4 * M)
    # The leavers carry the numbers they were picked on last time.
    assert result.went_out[0].total_fp == Decimal(40)


def test_an_import_without_a_result_is_skipped(season, players):
    first = team(season, 22, [(players["A"], 5, 50)])
    current = team(season, 26, [(players["B"], 5, 70)])

    assert previous_of(current) == first
    assert [pick.player.last_name for pick in changes(current).went_out] == ["A"]


def test_a_later_result_is_never_the_previous_one(season, players):
    current = team(season, 24, [(players["A"], 5, 50)])
    team(season, 26, [(players["B"], 5, 70)])

    assert changes(current).is_first


def test_another_seasons_result_is_never_the_previous_one(season, players):
    old_season = Season.objects.create(
        label="2025-26", starts_on=dt.date(2025, 10, 21), ends_on=dt.date(2026, 4, 12)
    )
    team(old_season, 20, [(players["A"], 5, 50)])
    current = team(season, 26, [(players["B"], 5, 70)])

    assert changes(current).is_first


def test_the_comparison_follows_a_recalculated_previous_result(season, players):
    """Not stored, so recalculating the older import changes what the newer
    one is compared with -- there is no stale comparison to go wrong."""
    previous = team(season, 24, [(players["A"], 5, 50)])
    current = team(season, 26, [(players["A"], 5, 60)])
    assert changes(current).unchanged

    previous.picks.all().delete()
    DreamTeamPick.objects.create(
        dream_team=previous,
        player=players["F"],
        position="G",
        salary=Decimal(5 * M),
        total_fp=Decimal(55),
        games_played=3,
    )

    result = changes(current)
    assert [pick.player.last_name for pick in result.came_in] == ["A"]
    assert [pick.player.last_name for pick in result.went_out] == ["F"]


def test_comparing_costs_a_fixed_number_of_queries(season, players, django_assert_num_queries):
    team(season, 24, [(players[name], 1, 10) for name in "ABCDE"])
    current = team(season, 26, [(players[name], 1, 10) for name in "CDEFG"])
    current = DreamTeam.objects.prefetch_related("picks__player").get(pk=current.pk)

    # The previous result, its picks, their players; the current one is prefetched.
    with django_assert_num_queries(3):
        result = changes(current)
        names = [pick.player.last_name for pick in result.came_in + result.went_out]

    assert sorted(names) == ["A", "B", "F", "G"]
