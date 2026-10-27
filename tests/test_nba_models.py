import pytest
from django.db.utils import IntegrityError
from django.utils import timezone

from apps.nba.models import Player, Team


@pytest.fixture
def lakers(db):
    # Every database has the 30 teams from migration nba.0013.
    return Team.objects.get(abbreviation="LAL")


def test_team_str(lakers):
    assert str(lakers) == "Los Angeles Lakers"


def test_team_gets_a_uuid_primary_key(lakers):
    import uuid

    assert isinstance(lakers.pk, uuid.UUID)


def test_abbreviation_is_unique(lakers):
    with pytest.raises(IntegrityError):
        Team.objects.create(name="Other Team", abbreviation="LAL")


def test_player_full_name_survives_a_missing_first_name(db):
    player = Player.objects.create(last_name="Nenê", position=Player.Position.CENTER)
    assert player.full_name == "Nenê"


def test_slug_is_generated_from_the_name_and_strips_accents(db):
    player = Player.objects.create(
        first_name="Luka", last_name="Dončić", position=Player.Position.GUARD
    )
    assert player.slug == "luka-doncic"


def test_colliding_names_get_a_numbered_slug(db):
    first = Player.objects.create(
        first_name="Chris", last_name="Johnson", position=Player.Position.FORWARD
    )
    second = Player.objects.create(
        first_name="Chris", last_name="Johnson", position=Player.Position.GUARD
    )
    assert first.slug == "chris-johnson"
    assert second.slug == "chris-johnson-2"


def test_slug_survives_a_corrected_name(db):
    player = Player.objects.create(
        first_name="Jarret", last_name="Allen", position=Player.Position.CENTER
    )
    player.first_name = "Jarrett"
    player.save()
    player.refresh_from_db()
    assert player.slug == "jarret-allen"


def test_deleting_a_team_keeps_its_players(lakers):
    player = Player.objects.create(
        first_name="LeBron",
        last_name="James",
        position=Player.Position.FORWARD,
        team=lakers,
    )
    lakers.delete()
    player.refresh_from_db()
    assert player.team is None


@pytest.fixture
def season(db):
    from datetime import date

    from apps.fantasy.models import Season

    return Season.objects.create(
        label="2025-26", starts_on=date(2025, 10, 1), ends_on=date(2026, 6, 30), is_current=True
    )


def _record_days(player, season, days):
    """One snapshot per day, each a `(total_fp, games_played)` pair."""
    from datetime import timedelta

    from apps.fantasy.models import PlayerSnapshot

    start = timezone.now()
    for index, (total_fp, games_played) in enumerate(days):
        PlayerSnapshot.objects.create(
            player=player,
            season=season,
            as_of=start + timedelta(days=index),
            salary=1,
            total_fp=total_fp,
            games_played=games_played,
            position=player.position,
        )


def test_hotness_score_does_not_count_an_unchanged_average_as_a_rise(season):
    player = Player.objects.create(last_name="Form", position=Player.Position.GUARD)
    averages = [10, 12, 12, 9, 11]
    _record_days(
        player, season, [(fp * (games + 1), games + 1) for games, fp in enumerate(averages)]
    )

    assert player.hotness_score() == "2/4"


def test_hotness_score_uses_only_the_latest_eleven_games(season):
    player = Player.objects.create(last_name="Recent", position=Player.Position.GUARD)
    # The first game's average is the highest, so it would break the streak.
    _record_days(
        player, season, [(100, 1), *(((games) * (games), games) for games in range(2, 13))]
    )

    assert player.hotness_score() == "10/10"


def test_hotness_score_skips_days_without_a_game(season):
    player = Player.objects.create(last_name="Rested", position=Player.Position.GUARD)
    # Rising after every game, with a rest day between games.
    _record_days(player, season, [(10, 1), (10, 1), (22, 2), (22, 2), (36, 3), (36, 3)])

    assert player.hotness_score() == "2/2"


def test_hotness_score_counts_ten_games_from_many_daily_snapshots(season):
    player = Player.objects.create(last_name="Daily", position=Player.Position.GUARD)
    days = []
    for games in range(1, 13):
        days += [(games * games, games), (games * games, games)]
    _record_days(player, season, [(0, 0), *days])

    assert player.hotness_score() == "10/10"


def test_hotness_score_counts_games_between_two_downloads_once(season):
    player = Player.objects.create(last_name="Missed", position=Player.Position.GUARD)
    # Two games land between the second and third snapshot.
    _record_days(player, season, [(10, 1), (22, 2), (48, 4)])

    assert player.hotness_score() == "2/2"


def test_hotness_score_ignores_a_corrected_games_count(season):
    player = Player.objects.create(last_name="Corrected", position=Player.Position.GUARD)
    # The source briefly reports one game fewer; that row is not a game.
    _record_days(player, season, [(10, 1), (22, 2), (5, 1), (36, 3)])

    assert player.hotness_score() == "2/2"


def test_hotness_score_is_none_before_the_first_game(season):
    player = Player.objects.create(last_name="Bench", position=Player.Position.GUARD)
    _record_days(player, season, [(0, 0), (0, 0), (10, 1), (10, 1)])

    assert player.hotness_score() is None


def test_hotness_score_reads_prefetched_snapshots_the_same_way(season):
    from django.db.models import Prefetch

    from apps.fantasy.models import PlayerSnapshot

    player = Player.objects.create(last_name="Cached", position=Player.Position.GUARD)
    _record_days(player, season, [(10, 1), (10, 1), (22, 2), (22, 2), (30, 3), (52, 4)])
    prefetched = Player.objects.prefetch_related(
        Prefetch("snapshots", queryset=PlayerSnapshot.objects.order_by("-as_of"))
    ).get(pk=player.pk)

    assert player.hotness_score() == "2/3"
    assert prefetched.hotness_score() == "2/3"


def test_hotness_score_does_not_compare_across_seasons(db):
    from datetime import date, timedelta

    from apps.fantasy.models import PlayerSnapshot, Season

    prior = Season.objects.create(
        label="2024-25", starts_on=date(2024, 10, 1), ends_on=date(2025, 6, 30)
    )
    current = Season.objects.create(
        label="2025-26", starts_on=date(2025, 10, 1), ends_on=date(2026, 6, 30), is_current=True
    )
    player = Player.objects.create(last_name="Seasonal", position=Player.Position.GUARD)
    for season, day, total_fp, games in [
        (prior, 1, 100, 10),
        (current, 2, 10, 1),
    ]:
        PlayerSnapshot.objects.create(
            player=player,
            season=season,
            as_of=timezone.now() + timedelta(days=day),
            salary=1,
            total_fp=total_fp,
            games_played=games,
            position=player.position,
        )

    assert player.hotness_score() is None
