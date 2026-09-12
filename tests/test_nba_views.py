from decimal import Decimal

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone


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
def roster(teams, manager):
    from datetime import date

    from apps.fantasy.models import Roster, Season
    from apps.nba.models import Player, Team

    season = Season.objects.create(
        label="2025-26", starts_on=date(2025, 10, 1), ends_on=date(2026, 6, 30), is_current=True
    )
    lakers = Team.objects.get(abbreviation="LAL")
    celtics = Team.objects.get(abbreviation="BOS")
    Player.objects.create(first_name="LeBron", last_name="James", position="F", team=lakers)
    Player.objects.create(first_name="Luka", last_name="Dončić", position="G", team=lakers)
    Player.objects.create(first_name="Jayson", last_name="Tatum", position="F", team=celtics)
    Player.objects.create(first_name="Retired", last_name="Guy", position="C", is_active=False)
    return Roster.objects.create(manager=manager, season=season, name="Roster 1")


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


def test_filter_by_max_salary_in_millions(signed_in, roster):
    from apps.nba.models import Player

    Player.objects.filter(last_name="James").update(current_salary=10_000_000)
    Player.objects.filter(last_name="Dončić").update(current_salary=12_000_000)
    Player.objects.filter(last_name="Tatum").update(current_salary=8_000_000)

    response = signed_in.get(reverse("nba:player-list"), {"max_salary": "10"})

    assert names(response) == {"Jayson Tatum", "LeBron James"}


def test_expected_salary_and_difference_are_sortable(signed_in, roster):
    from apps.nba.models import Player

    Player.objects.filter(last_name="James").update(
        current_fp_per_game=20, current_salary=10_000_000
    )
    Player.objects.filter(last_name="Dončić").update(
        current_fp_per_game=10, current_salary=1_000_000
    )

    expected_response = signed_in.get(
        reverse("nba:player-list"), {"sort": "expected", "dir": "asc"}
    )
    difference_response = signed_in.get(
        reverse("nba:player-list"), {"sort": "difference", "dir": "desc"}
    )

    assert [player.full_name for player in expected_response.context["players"][:2]] == [
        "Luka Dončić",
        "LeBron James",
    ]
    assert [player.full_name for player in difference_response.context["players"][:2]] == [
        "Luka Dončić",
        "LeBron James",
    ]


def test_hotness_is_sortable(signed_in, roster):
    from datetime import timedelta

    from django.utils import timezone

    from apps.fantasy import services
    from apps.nba.models import Player

    players = list(Player.objects.filter(is_active=True).order_by("last_name"))
    for player, values in zip(players, ((10, 12, 14), (10, 9, 8), (10, 11, 10)), strict=True):
        for index, fp_per_game in enumerate(values):
            services.record_snapshot(
                player,
                roster.season,
                timezone.now() + timedelta(days=index),
                salary=1,
                total_fp=fp_per_game * (index + 1),
                games_played=index + 1,
                team=player.team,
                position=player.position,
            )

    ascending = signed_in.get(reverse("nba:player-list"), {"sort": "hotness", "dir": "asc"})
    descending = signed_in.get(
        reverse("nba:player-list"), {"sort": "hotness", "dir": "desc"}
    )

    assert [player.full_name for player in ascending.context["players"][:3]] == [
        "LeBron James",
        "Jayson Tatum",
        "Luka Dončić",
    ]
    assert [player.full_name for player in descending.context["players"][:3]] == [
        "Luka Dončić",
        "Jayson Tatum",
        "LeBron James",
    ]


def test_predict_salary_matches_formula_and_floor():
    from apps.nba.models import predict_salary

    assert predict_salary(Decimal("20")) == Decimal("6.08")
    assert predict_salary(Decimal("-1")) == Decimal("0.00")


def test_player_predicted_salary_uses_current_points_per_game(db):
    from apps.nba.models import Player

    player = Player(
        first_name="Expected", last_name="Salary", position=Player.Position.GUARD
    )

    assert player.predicted_salary is None

    player.current_fp_per_game = Decimal("20")
    assert player.predicted_salary == Decimal("6.08")


def test_filter_by_owned_roster(signed_in, roster, manager, other_manager):
    from apps.fantasy.models import Roster, RosterPlayer
    from apps.nba.models import Player

    second_roster = Roster.objects.create(manager=manager, season=roster.season, name="Roster 2")
    rival_roster = Roster.objects.create(manager=other_manager, season=roster.season, name="Rival")
    players = list(Player.objects.filter(is_active=True).order_by("last_name"))
    RosterPlayer.objects.create(roster=roster, player=players[0], added_at=timezone.now())
    RosterPlayer.objects.create(roster=second_roster, player=players[1], added_at=timezone.now())
    RosterPlayer.objects.create(roster=rival_roster, player=players[2], added_at=timezone.now())

    response = signed_in.get(reverse("nba:player-list"), {"roster": second_roster.pk})

    assert names(response) == {players[1].full_name}
    assert {choice.pk for choice in response.context["rosters"]} == {roster.pk, second_roster.pk}


def test_roster_filter_cannot_select_another_users_roster(signed_in, roster, other_manager):
    from apps.fantasy.models import Roster, RosterPlayer
    from apps.nba.models import Player

    rival_roster = Roster.objects.create(manager=other_manager, season=roster.season, name="Rival")
    player = Player.objects.filter(is_active=True).first()
    RosterPlayer.objects.create(roster=rival_roster, player=player, added_at=timezone.now())

    response = signed_in.get(reverse("nba:player-list"), {"roster": rival_roster.pk})

    assert names(response) == {"Jayson Tatum", "LeBron James", "Luka Dončić"}


def test_scatter_chart_uses_the_filtered_players(signed_in, roster):
    from apps.nba.models import Player

    Player.objects.filter(last_name="James").update(
        current_fp_per_game=12,
        current_salary=10_000_000,
    )
    Player.objects.filter(last_name="Tatum").update(
        current_fp_per_game=9,
        current_salary=8_000_000,
    )

    response = signed_in.get(reverse("nba:player-list"), {"team": "BOS"})

    assert [dot["label"] for dot in response.context["salary_fp_chart"]["dots"]] == [
        "Jayson Tatum"
    ]
    dot = response.context["salary_fp_chart"]["dots"][0]
    assert dot["detail_url"] == reverse(
        "nba:player-detail", args=[Player.objects.get(last_name="Tatum").slug]
    )
    assert dot["hotness"] is None


def test_filter_by_at_least_six_games(signed_in, roster):
    from apps.nba.models import Player

    Player.objects.filter(last_name="James").update(current_games_played=5)
    Player.objects.filter(last_name="Dončić").update(current_games_played=6)
    Player.objects.filter(last_name="Tatum").update(current_games_played=7)

    response = signed_in.get(reverse("nba:player-list"), {"min_games": "6"})

    assert names(response) == {"Luka Dončić", "Jayson Tatum"}


def test_an_unknown_position_is_ignored_rather_than_erroring(signed_in, roster):
    response = signed_in.get(reverse("nba:player-list"), {"position": "X"})
    assert response.status_code == 200
    assert len(names(response)) == 3


def test_htmx_request_returns_only_the_table(signed_in, roster):
    response = signed_in.get(reverse("nba:player-list"), headers={"HX-Request": "true"})
    assert response.templates[0].name == "nba/partials/player_table.html"


def test_player_list_displays_hotness_score(signed_in, roster):
    from datetime import timedelta

    from django.utils import timezone

    from apps.fantasy import services
    from apps.nba.models import Player

    player = Player.objects.get(last_name="James")
    for index, fp_per_game in enumerate((10, 12, 11)):
        services.record_snapshot(
            player,
            roster.season,
            timezone.now() + timedelta(days=index),
            salary=1,
            total_fp=fp_per_game * (index + 1),
            games_played=index + 1,
            team=player.team,
            position=player.position,
        )

    body = signed_in.get(reverse("nba:player-list")).content.decode()

    assert "Hotness" in body
    assert "1/2" in body


def test_player_table_uses_team_abbreviations_and_right_aligns_non_name_columns(
    signed_in, roster
):
    body = signed_in.get(reverse("nba:player-list")).content.decode()

    assert "LAL" in body
    assert 'class="ml-1.5"' not in body
    assert '<th scope="col" class="px-4 py-3 text-right font-medium">Status</th>' in body


def test_empty_state_mentions_the_missing_import(signed_in, teams):
    body = signed_in.get(reverse("nba:player-list")).content.decode()
    assert "basketball.de" in body
