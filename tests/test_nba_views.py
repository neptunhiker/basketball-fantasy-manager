import pytest
from django.core.management import call_command
from django.urls import reverse


@pytest.fixture
def teams(db):
    call_command("seed_teams", verbosity=0)


def test_team_list_requires_login(client, teams):
    response = client.get(reverse("nba:team-list"))
    assert response.status_code == 302
    assert reverse("accounts:login") in response.url


def test_team_list_shows_every_team(client, user, password, teams):
    client.login(email=user.email, password=password)
    response = client.get(reverse("nba:team-list"))
    assert response.status_code == 200
    assert len(response.context["teams"]) == 30


def test_team_list_shows_conference_and_division(client, user, password, teams):
    client.login(email=user.email, password=password)
    body = client.get(reverse("nba:team-list")).content.decode()
    assert "Los Angeles Lakers" in body
    assert "Western Conference" in body
    assert "Pacific" in body


def test_team_list_is_grouped_east_before_west(client, user, password, teams):
    client.login(email=user.email, password=password)
    names = [t.name for t in client.get(reverse("nba:team-list")).context["teams"]]
    assert names[0] == "Boston Celtics"  # East / Atlantic, alphabetically first
    assert names[-1] == "San Antonio Spurs"  # West / Southwest, alphabetically last


def test_empty_state_points_at_the_seed_command(client, user, password, db):
    client.login(email=user.email, password=password)
    body = client.get(reverse("nba:team-list")).content.decode()
    assert "seed_teams" in body


@pytest.fixture
def roster(teams):
    from apps.nba.models import Player, Team

    lakers = Team.objects.get(abbreviation="LAL")
    celtics = Team.objects.get(abbreviation="BOS")
    Player.objects.create(first_name="LeBron", last_name="James", position="F", team=lakers)
    Player.objects.create(first_name="Luka", last_name="Dončić", position="G", team=lakers)
    Player.objects.create(first_name="Jayson", last_name="Tatum", position="F", team=celtics)
    Player.objects.create(first_name="Retired", last_name="Guy", position="C", is_active=False)


@pytest.fixture
def signed_in(client, user, password, db):
    client.login(email=user.email, password=password)
    return client


def names(response):
    return {p.full_name for p in response.context["players"]}


def test_player_list_requires_login(client, db):
    response = client.get(reverse("nba:player-list"))
    assert response.status_code == 302


def test_hides_inactive_players_by_default(signed_in, roster):
    assert "Retired Guy" not in names(signed_in.get(reverse("nba:player-list")))


def test_inactive_players_can_be_shown(signed_in, roster):
    response = signed_in.get(reverse("nba:player-list"), {"inactive": "1"})
    assert "Retired Guy" in names(response)


def test_search_matches_across_first_and_last_name(signed_in, roster):
    response = signed_in.get(reverse("nba:player-list"), {"q": "james lebron"})
    assert names(response) == {"LeBron James"}


def test_filter_by_position(signed_in, roster):
    response = signed_in.get(reverse("nba:player-list"), {"position": "G"})
    assert names(response) == {"Luka Dončić"}


def test_filter_by_team(signed_in, roster):
    response = signed_in.get(reverse("nba:player-list"), {"team": "BOS"})
    assert names(response) == {"Jayson Tatum"}


def test_an_unknown_position_is_ignored_rather_than_erroring(signed_in, roster):
    response = signed_in.get(reverse("nba:player-list"), {"position": "X"})
    assert response.status_code == 200
    assert len(names(response)) == 3


def test_htmx_request_returns_only_the_table(signed_in, roster):
    response = signed_in.get(reverse("nba:player-list"), headers={"HX-Request": "true"})
    assert response.templates[0].name == "nba/partials/player_table.html"


def test_empty_state_mentions_the_missing_import(signed_in, teams):
    body = signed_in.get(reverse("nba:player-list")).content.decode()
    assert "basketball.de" in body
