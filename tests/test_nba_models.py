import pytest
from django.db.utils import IntegrityError
from django.utils import timezone

from apps.nba.models import Player, Team


@pytest.fixture
def lakers(db):
    return Team.objects.create(
        name="Los Angeles Lakers",
        abbreviation="LAL",
        conference=Team.Conference.WEST,
        division=Team.Division.PACIFIC,
    )


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


def test_hotness_score_counts_increases_and_equals_in_recent_snapshots(db):
    from datetime import date, timedelta

    from apps.fantasy.models import PlayerSnapshot, Season

    season = Season.objects.create(
        label="2025-26", starts_on=date(2025, 10, 1), ends_on=date(2026, 6, 30), is_current=True
    )
    player = Player.objects.create(last_name="Form", position=Player.Position.GUARD)
    values = [10, 12, 12, 9, 11]
    for index, fp_per_game in enumerate(values):
        PlayerSnapshot.objects.create(
            player=player,
            season=season,
            as_of=timezone.now() + timedelta(days=index),
            salary=1,
            total_fp=fp_per_game * (index + 1),
            games_played=index + 1,
            position=player.position,
        )

    assert player.hotness_score() == "2/4"


def test_hotness_score_uses_only_the_latest_eleven_snapshots(db):
    from datetime import date, timedelta

    from apps.fantasy.models import PlayerSnapshot, Season

    season = Season.objects.create(
        label="2025-26", starts_on=date(2025, 10, 1), ends_on=date(2026, 6, 30), is_current=True
    )
    player = Player.objects.create(last_name="Recent", position=Player.Position.GUARD)
    for index in range(12):
        PlayerSnapshot.objects.create(
            player=player,
            season=season,
            as_of=timezone.now() + timedelta(days=index),
            salary=1,
            total_fp=(index + 1) * (index + 1),
            games_played=index + 1,
            position=player.position,
        )

    assert player.hotness_score() == "10/10"


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
