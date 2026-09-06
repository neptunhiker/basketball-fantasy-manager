import pytest
from django.db.utils import IntegrityError

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
