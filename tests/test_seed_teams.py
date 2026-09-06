import pytest
from django.core.management import call_command

from apps.nba.models import Team


@pytest.fixture
def seeded(db):
    call_command("seed_teams", verbosity=0)


def test_creates_thirty_teams(seeded):
    assert Team.objects.count() == 30


def test_every_division_has_five_teams(seeded):
    for division in Team.Division:
        assert Team.objects.filter(division=division).count() == 5, division


def test_conferences_are_evenly_split(seeded):
    assert Team.objects.filter(conference=Team.Conference.EAST).count() == 15
    assert Team.objects.filter(conference=Team.Conference.WEST).count() == 15


def test_running_twice_does_not_duplicate(seeded):
    call_command("seed_teams", verbosity=0)
    assert Team.objects.count() == 30


def test_rerun_repairs_an_edited_team(seeded):
    Team.objects.filter(abbreviation="LAL").update(name="Wrong", division="")
    call_command("seed_teams", verbosity=0)
    lakers = Team.objects.get(abbreviation="LAL")
    assert lakers.name == "Los Angeles Lakers"
    assert lakers.division == Team.Division.PACIFIC
