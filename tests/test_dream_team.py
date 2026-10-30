"""The Dream Team as stored per import: candidates, refresh, overwriting."""

import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.analytics.dream_team import NoDreamTeam, candidates, draft_moment, refresh
from apps.analytics.models import DreamTeam
from apps.fantasy.models import PlayerSnapshot, Season
from apps.nba.models import Player, Team

pytestmark = pytest.mark.django_db

BERLIN = ZoneInfo("Europe/Berlin")
PRESEASON = dt.datetime(2026, 10, 13, 20, 0, tzinfo=BERLIN)
FIRST_IMPORT = dt.datetime(2026, 10, 24, 12, 15, tzinfo=BERLIN)
SECOND_IMPORT = dt.datetime(2026, 10, 26, 12, 15, tzinfo=BERLIN)
M = 1_000_000


@pytest.fixture
def season(db):
    return Season.objects.create(
        label="2026-27",
        starts_on=dt.date(2026, 10, 21),
        ends_on=dt.date(2027, 4, 12),
        is_current=True,
        starting_cash=Decimal(63 * M),
    )


def snap(player, season, as_of, salary_m, fp=0, games=0, position=None):
    return PlayerSnapshot.objects.create(
        player=player,
        season=season,
        as_of=as_of,
        salary=Decimal(str(salary_m)) * M,
        total_fp=Decimal(str(fp)),
        games_played=games,
        position=position or player.position,
        team=player.team,
    )


def player(name, position):
    return Player.objects.create(
        first_name="P", last_name=name, position=position, team=Team.objects.get(abbreviation="LAL")
    )


@pytest.fixture
def league(season):
    """Seven of each outfield position and three Centers, $4M each on draft
    day, so any 15 fit the $63M. Points in the first import rise with the
    number in the name, Centers 100 ahead; the second import adds 10 to
    everyone. So the two left out are always G1 and F1."""
    players = {}
    for position, count, bonus in (("G", 7, 0), ("F", 7, 0), ("C", 3, 100)):
        for number in range(1, count + 1):
            name = f"{position}{number}"
            players[name] = p = player(name, position)
            snap(p, season, PRESEASON, 4)
            snap(p, season, FIRST_IMPORT, 4, fp=bonus + number * 10, games=2)
            snap(p, season, SECOND_IMPORT, 4, fp=bonus + number * 10 + 10, games=3)
    return players


def names(dream_team):
    return {pick.player.last_name for pick in dream_team.picks.select_related("player")}


# --- draft day ---------------------------------------------------------------


def test_draft_day_is_tipoff_at_midnight_without_a_signings_date(season):
    assert draft_moment(season) == dt.datetime(2026, 10, 21, 0, 0, tzinfo=BERLIN)


def test_draft_day_is_when_signings_opened_when_that_is_set(season):
    season.signings_open_at = dt.datetime(2026, 10, 10, 18, 0, tzinfo=BERLIN)
    assert draft_moment(season) == season.signings_open_at


# --- candidates --------------------------------------------------------------


def test_candidates_carry_draft_price_and_position_but_current_points(season):
    p = player("Mover", "G")
    snap(p, season, PRESEASON, 3, position="G")
    snap(p, season, FIRST_IMPORT, 9, fp=55, games=2, position="F")

    draft_as_of, pool = candidates(season, FIRST_IMPORT)

    assert draft_as_of == PRESEASON
    (candidate,) = pool
    assert candidate.salary == Decimal(3 * M)
    assert candidate.position == "G"
    assert candidate.points == Decimal(55)
    assert candidate.games_played == 2


def test_players_without_a_draft_day_price_are_not_candidates(season):
    drafted = player("Drafted", "G")
    snap(drafted, season, PRESEASON, 3)
    late = player("Late", "G")
    snap(late, season, FIRST_IMPORT, 1, fp=99, games=2)

    _, pool = candidates(season, FIRST_IMPORT)

    assert [c.player_id for c in pool] == [drafted.pk]


def test_points_come_from_the_import_asked_for_not_a_later_one(season):
    p = player("Steady", "C")
    snap(p, season, PRESEASON, 2)
    snap(p, season, FIRST_IMPORT, 2, fp=20, games=1)
    snap(p, season, SECOND_IMPORT, 2, fp=45, games=2)

    _, pool = candidates(season, FIRST_IMPORT)

    assert pool[0].points == Decimal(20)


def test_prices_come_from_the_last_snapshot_before_signings_opened(season):
    season.signings_open_at = dt.datetime(2026, 10, 15, 18, 0, tzinfo=BERLIN)
    p = player("Repriced", "F")
    snap(p, season, PRESEASON, 3)
    snap(p, season, dt.datetime(2026, 10, 14, 12, 0, tzinfo=BERLIN), 5)
    snap(p, season, dt.datetime(2026, 10, 16, 12, 0, tzinfo=BERLIN), 8)

    draft_as_of, pool = candidates(season, FIRST_IMPORT)

    assert draft_as_of == dt.datetime(2026, 10, 14, 12, 0, tzinfo=BERLIN)
    assert pool[0].salary == Decimal(5 * M)


# --- refresh -----------------------------------------------------------------


def test_refresh_stores_the_best_team_of_the_newest_import(season, league):
    dream_team = refresh(season)

    assert dream_team.as_of == SECOND_IMPORT
    assert dream_team.draft_as_of == PRESEASON
    assert names(dream_team) == {
        *(f"G{n}" for n in range(2, 8)),
        *(f"F{n}" for n in range(2, 8)),
        "C1",
        "C2",
        "C3",
    }
    assert dream_team.picks.count() == 15
    assert dream_team.total_salary == Decimal(60 * M)
    assert dream_team.budget == Decimal(63 * M)
    assert dream_team.left_over == Decimal(3 * M)
    assert dream_team.candidates == 17
    assert dream_team.total_fp == sum(p.total_fp for p in dream_team.picks.all())


def test_each_pick_keeps_the_numbers_it_was_chosen_on(season, league):
    dream_team = refresh(season)
    pick = dream_team.picks.get(player=league["G7"])

    assert pick.position == "G"
    assert pick.salary == Decimal(4 * M)
    assert pick.total_fp == Decimal(80)
    assert pick.games_played == 3
    assert pick.fp_per_million == Decimal(20)


def test_refresh_for_an_earlier_import_uses_that_imports_points(season, league):
    dream_team = refresh(season, FIRST_IMPORT)

    assert dream_team.as_of == FIRST_IMPORT
    assert dream_team.picks.get(player=league["G7"]).total_fp == Decimal(70)


def test_a_moment_between_imports_counts_as_the_import_before_it(season, league):
    dream_team = refresh(season, FIRST_IMPORT + dt.timedelta(hours=5))

    assert dream_team.as_of == FIRST_IMPORT


def test_recalculating_an_import_overwrites_its_result(season, league):
    first = refresh(season)
    # A late correction makes G1 the best Guard by far.
    PlayerSnapshot.objects.filter(player=league["G1"], as_of=SECOND_IMPORT).update(
        total_fp=Decimal(500)
    )

    second = refresh(season)

    assert DreamTeam.objects.count() == 1
    assert second.pk == first.pk
    assert "G1" in names(second)
    assert second.picks.count() == 15


def test_every_import_keeps_its_own_result(season, league):
    refresh(season, FIRST_IMPORT)
    refresh(season, SECOND_IMPORT)

    assert list(DreamTeam.objects.values_list("as_of", flat=True)) == [
        SECOND_IMPORT,
        FIRST_IMPORT,
    ]


def test_a_stored_result_keeps_the_budget_it_was_chosen_under(season, league):
    dream_team = refresh(season)
    season.starting_cash = Decimal(70 * M)
    season.save()

    dream_team.refresh_from_db()
    assert dream_team.budget == Decimal(63 * M)


# --- nothing to work out -----------------------------------------------------


def test_no_dream_team_without_any_stats(season):
    with pytest.raises(NoDreamTeam):
        refresh(season)


def test_no_dream_team_without_draft_day_prices(season):
    snap(player("Late", "G"), season, FIRST_IMPORT, 1, fp=10, games=1)

    with pytest.raises(NoDreamTeam):
        refresh(season)


def test_no_dream_team_before_anyone_has_played(season, league):
    with pytest.raises(NoDreamTeam):
        refresh(season, PRESEASON)
    assert not DreamTeam.objects.exists()


def test_no_dream_team_when_no_legal_team_exists(season, league):
    PlayerSnapshot.objects.filter(player__position="C").delete()

    with pytest.raises(NoDreamTeam):
        refresh(season)


def test_a_result_that_no_longer_holds_is_removed(season, league):
    refresh(season)
    # The admin moves the draft date before the first snapshot: no prices.
    season.signings_open_at = PRESEASON - dt.timedelta(days=1)
    season.save()

    with pytest.raises(NoDreamTeam):
        refresh(season)
    assert not DreamTeam.objects.exists()
