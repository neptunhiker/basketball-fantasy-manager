from decimal import Decimal

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.nba.models import NbaApiUsage, Player
from apps.nba.services import DailyApiLimitExceeded, PROVIDER


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


def test_filter_by_injury_status(signed_in, roster):
    from django.utils import timezone

    from apps.nba.models import Player, PlayerInjury

    player = Player.objects.get(last_name="James")
    PlayerInjury.objects.create(
        player=player,
        observed_at=timezone.now(),
        status="Out",
        injury_type="Foot",
        short_comment="Test injury",
    )

    injured_response = signed_in.get(reverse("nba:player-list"), {"injury_status": "injured"})
    healthy_response = signed_in.get(reverse("nba:player-list"), {"injury_status": "healthy"})

    assert names(injured_response) == {"LeBron James"}
    assert names(healthy_response) == {"Jayson Tatum", "Luka Dončić"}


def test_player_list_injury_status_shows_the_latest_api_request(signed_in, roster):
    usage = NbaApiUsage.objects.create(
        provider=PROVIDER,
        usage_date=timezone.localdate(),
        request_count=1,
    )

    response = signed_in.get(reverse("nba:player-list"))

    assert response.context["last_injury_api_call"] == usage.updated_at
    assert "Last API request:" in response.content.decode()


def test_player_list_allows_an_injury_refresh_before_todays_call(signed_in, roster):
    response = signed_in.get(reverse("nba:player-list"))

    body = response.content.decode()
    assert "Update injuries" in body
    assert 'hx-target="closest .injury-refresh"' in body
    assert "data-global-loading" in body
    assert response.context["injury_refresh_available"] is True


def test_player_list_disables_injury_refresh_after_todays_call(signed_in, roster):
    NbaApiUsage.objects.create(
        provider=PROVIDER,
        usage_date=timezone.localdate(),
        request_count=1,
    )

    body = signed_in.get(reverse("nba:player-list")).content.decode()

    assert 'disabled aria-describedby="injury-refresh-limit"' in body
    assert "The next update can be invoked tomorrow." in body


def test_injury_refresh_runs_the_sync_and_returns_an_inline_confirmation(
    signed_in, roster, monkeypatch
):
    monkeypatch.setattr(
        "apps.nba.views.sync_injuries",
        lambda: {"created": 2, "updated": 1, "unmatched": 0, "ambiguous": 0},
    )

    response = signed_in.post(
        reverse("nba:injury-refresh"), HTTP_HX_REQUEST="true"
    )

    assert response.status_code == 200
    assert "Injury report updated: 2 created, 1 updated, 0 unmatched." in response.content.decode()


def test_injury_refresh_shows_the_daily_limit_message(signed_in, roster, monkeypatch):
    def raise_limit():
        raise DailyApiLimitExceeded("limit reached")

    monkeypatch.setattr("apps.nba.views.sync_injuries", raise_limit)

    response = signed_in.post(
        reverse("nba:injury-refresh"), HTTP_HX_REQUEST="true"
    )

    assert response.status_code == 200
    assert "The next update can be invoked tomorrow." in response.content.decode()


def test_filter_by_max_salary_in_millions(signed_in, roster):
    from apps.nba.models import Player

    Player.objects.filter(last_name="James").update(current_salary=10_000_000)
    Player.objects.filter(last_name="Dončić").update(current_salary=12_000_000)
    Player.objects.filter(last_name="Tatum").update(current_salary=8_000_000)

    response = signed_in.get(reverse("nba:player-list"), {"max_salary": "10"})

    assert names(response) == {"Jayson Tatum", "LeBron James"}


def test_compare_page_allows_up_to_five_selected_players(signed_in, roster):
    james = Player.objects.get(last_name="James")
    luka = Player.objects.get(last_name="Dončić")
    tatum = Player.objects.get(last_name="Tatum")

    response = signed_in.get(
        reverse("nba:player-compare"),
        {
            "player": [james.slug, luka.slug, tatum.slug],
            "q": "james",
        },
    )

    assert response.status_code == 200
    assert [player.slug for player in response.context["selected_players"]] == [
        james.slug,
        luka.slug,
        tatum.slug,
    ]
    assert response.context["search_query"] == "james"
    assert response.context["max_compare_players"] == 5


def test_compare_page_shows_salary_in_millions(signed_in, roster):
    james = Player.objects.get(last_name="James")
    james.current_salary = 10_000_000
    james.save(update_fields=["current_salary"])

    response = signed_in.get(reverse("nba:player-compare"), {"player": [james.slug]})

    assert response.status_code == 200
    body = response.content.decode()
    assert "$10.00M" in body
    assert 'title="$10,000,000"' in body


def test_compare_page_empty_state_and_modal_trigger(signed_in, roster):
    response = signed_in.get(reverse("nba:player-compare"))
    assert response.status_code == 200
    body = response.content.decode()
    assert "No players selected yet" in body
    assert reverse("nba:player-compare-modal") in body
    assert "hx-target=\"#modal-root\"" in body


def test_compare_page_selected_players_removal_link(signed_in, roster):
    james = Player.objects.get(last_name="James")
    luka = Player.objects.get(last_name="Dončić")

    response = signed_in.get(
        reverse("nba:player-compare"),
        {"player": [james.slug, luka.slug]},
    )
    assert response.status_code == 200
    body = response.content.decode()
    assert james.full_name in body
    assert luka.full_name in body
    # Table headers match players page
    assert "Actual salary" in body
    assert "Expected salary" in body
    assert "Difference" in body
    assert "Points" in body
    assert "Avg/game" in body
    assert "Games" in body
    assert "Hotness" in body
    assert "Injury" in body
    assert "Status" in body
    # Removal link for James leaves Luka
    assert f"player={luka.slug}" in body


def test_compare_modal_search_and_exclusions(signed_in, roster):
    james = Player.objects.get(last_name="James")
    luka = Player.objects.get(last_name="Dončić")

    # Initial modal load excluding James
    modal_response = signed_in.get(
        reverse("nba:player-compare-modal"),
        {"player": [james.slug], "q": "Dončić"},
    )
    assert modal_response.status_code == 200
    body = modal_response.content.decode()
    assert luka.full_name in body
    assert f"player={james.slug}&amp;player={luka.slug}" in body

    # HTMX body request
    body_response = signed_in.get(
        reverse("nba:player-compare-modal"),
        {"player": [james.slug], "q": "Dončić", "body": "1"},
    )
    assert body_response.status_code == 200
    assert "compare-modal-results" in body_response.content.decode()


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
    assert '<th scope="col" class="px-4 py-3 text-right font-medium">Injury</th>' in body
    assert '<th scope="col" class="px-4 py-3 text-right font-medium">Status</th>' in body


def test_player_list_shows_latest_injury_in_clickable_column(signed_in, roster):
    from django.utils import timezone

    from apps.nba.models import Player, PlayerInjury

    player = Player.objects.get(last_name="James")
    PlayerInjury.objects.create(
        player=player,
        observed_at=timezone.now(),
        status="Out",
        injury_type="Foot",
        short_comment="Test injury",
    )

    body = signed_in.get(reverse("nba:player-list")).content.decode()

    assert "Injured" in body
    assert "Latest injury report" in body
    assert "Test injury" in body


def test_empty_state_mentions_the_missing_import(signed_in, teams):
    body = signed_in.get(reverse("nba:player-list")).content.decode()
    assert "basketball.de" in body
