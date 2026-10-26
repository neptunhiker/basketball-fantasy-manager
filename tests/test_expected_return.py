"""Expected return: the injury report's date as a column, a sort and a filter."""

import datetime as dt

import pytest
from django.core.management import call_command
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone, translation

from apps.fantasy import services
from apps.fantasy.models import Roster, Season
from apps.nba.models import Player, PlayerInjury, Team
from apps.nba.templatetags.player_stats import days_until, short_month_day

pytestmark = pytest.mark.django_db

LIST = reverse("nba:player-list")


def days(n):
    return timezone.localdate() + dt.timedelta(days=n)


def report(player, status, return_date=None, minutes_ago=0):
    return PlayerInjury.objects.create(
        player=player,
        observed_at=timezone.now() - dt.timedelta(minutes=minutes_ago),
        status=status,
        return_date=return_date,
    )


@pytest.fixture
def players(db):
    call_command("seed_teams", verbosity=0)
    lakers = Team.objects.get(abbreviation="LAL")
    made = {
        name: Player.objects.create(
            first_name="P", last_name=name, position="G", team=lakers, current_salary=1_000_000
        )
        for name in ("Soon", "Later", "Unknown", "Healthy", "Recovered")
    }
    report(made["Soon"], "Day-To-Day", days(1))
    report(made["Later"], "Out", days(10))
    report(made["Unknown"], "Out")
    # Back from an injury that still carries its old date.
    report(made["Recovered"], "Out", days(3), minutes_ago=60)
    report(made["Recovered"], "Healthy")
    return made


@pytest.fixture
def signed_in(client, user, password):
    client.login(email=user.email, password=password)
    return client


def listed(response):
    return [player.last_name for player in response.context["players"]]


# --- the model -----------------------------------------------------------------------------


def test_expected_return_comes_from_the_latest_report_while_hurt(players):
    assert players["Soon"].expected_return == days(1)
    assert players["Soon"].return_in_days == 1
    assert players["Unknown"].expected_return is None


def test_a_healthy_player_has_no_expected_return(players):
    assert players["Healthy"].expected_return is None
    assert players["Recovered"].expected_return is None
    assert players["Recovered"].return_in_days is None


# --- sorting -------------------------------------------------------------------------------


def test_the_player_list_sorts_by_return_soonest_first(signed_in, players):
    order = listed(signed_in.get(LIST, {"sort": "return"}))
    assert order[:2] == ["Soon", "Later"]
    assert set(order[2:]) == {"Unknown", "Healthy", "Recovered"}


def test_players_without_a_return_date_stay_last_in_descending_order(signed_in, players):
    order = listed(signed_in.get(LIST, {"sort": "return", "dir": "desc"}))
    assert order[:2] == ["Later", "Soon"]


def test_the_team_page_sorts_by_return_too(signed_in, players):
    response = signed_in.get(
        reverse("nba:team-detail", args=["lal"]), {"sort": "return", "dir": "desc"}
    )
    assert response.status_code == 200
    assert [p.last_name for p in response.context["players"]][:2] == ["Later", "Soon"]


# --- filtering -----------------------------------------------------------------------------


def test_back_by_finds_players_returning_up_to_that_date(signed_in, players):
    response = signed_in.get(LIST, {"return_to": days(5).isoformat()})
    assert listed(response) == ["Soon"]


def test_back_from_finds_players_returning_on_or_after_that_date(signed_in, players):
    response = signed_in.get(LIST, {"return_from": days(10).isoformat()})
    assert listed(response) == ["Later"]


def test_both_bounds_make_a_window(signed_in, players):
    response = signed_in.get(
        LIST, {"return_from": days(1).isoformat(), "return_to": days(10).isoformat()}
    )
    assert sorted(listed(response)) == ["Later", "Soon"]


def test_healthy_and_undated_players_never_match_a_date_filter(signed_in, players):
    response = signed_in.get(LIST, {"return_to": days(365).isoformat()})
    assert sorted(listed(response)) == ["Later", "Soon"]


@pytest.mark.parametrize("value", ["", "soon", "2026-13-45"])
def test_an_invalid_date_is_ignored(signed_in, players, value):
    response = signed_in.get(LIST, {"return_to": value})
    assert response.status_code == 200
    assert len(listed(response)) == 5


def test_the_date_filter_combines_with_the_injury_status(signed_in, players):
    response = signed_in.get(LIST, {"injury_status": "injured", "return_to": days(30).isoformat()})
    assert listed(response) == ["Later"]


def test_the_date_filters_count_toward_the_phone_filter_badge(signed_in, players):
    response = signed_in.get(
        LIST, {"return_from": days(1).isoformat(), "return_to": days(5).isoformat()}
    )
    assert response.context["active_filter_count"] == 2


def test_the_filter_keeps_its_dates_after_a_reload(signed_in, players):
    body = signed_in.get(LIST, {"return_from": "2026-11-01"}).content.decode()
    assert 'name="return_from"' in body
    assert 'value="2026-11-01"' in body


# --- the column ----------------------------------------------------------------------------


def test_the_player_table_has_a_sortable_return_column(signed_in, players):
    body = signed_in.get(LIST).content.decode()
    assert "sort=return" in body


def test_the_cell_shows_date_and_distance(players):
    body = render_to_string("nba/partials/return_date.html", {"player": players["Later"]})
    assert f'datetime="{days(10).isoformat()}"' in body
    assert "in 10 days" in body


def test_the_cell_says_unknown_for_a_hurt_player_without_a_date(players):
    body = render_to_string("nba/partials/return_date.html", {"player": players["Unknown"]})
    assert "unknown" in body


def test_the_cell_shows_a_dash_for_a_healthy_player(players):
    body = render_to_string("nba/partials/return_date.html", {"player": players["Recovered"]})
    assert "\N{EN DASH}" in body
    assert "datetime" not in body


def test_a_date_gone_by_is_greyed(players):
    report(players["Soon"], "Out", days(-2))
    body = render_to_string("nba/partials/return_date.html", {"player": players["Soon"]})
    assert "2 days ago" in body
    assert "text-neutral-400" in body


# --- the squad -----------------------------------------------------------------------------


@pytest.fixture
def roster(manager, players):
    today = timezone.localdate()
    season = Season.objects.create(
        label="Soon",
        starts_on=today + dt.timedelta(days=13),
        ends_on=today + dt.timedelta(days=183),
        is_current=True,
    )
    roster = Roster.objects.create(manager=manager, season=season, name="R")
    for name in ("Healthy", "Later", "Soon"):
        services.buy(roster, players[name], players[name].current_salary)
    return roster


def test_the_squad_sorts_by_return_with_healthy_players_last(signed_in, roster):
    for direction, expected in (("asc", ["Soon", "Later"]), ("desc", ["Later", "Soon"])):
        response = signed_in.get(
            reverse("fantasy:roster-build", args=[roster.pk]),
            {"sort": "return", "dir": direction},
        )
        squad = [m.player.last_name for m in response.context["memberships"]]
        assert squad == [*expected, "Healthy"]


def test_the_squad_shows_the_return_in_table_and_cards(signed_in, roster):
    body = signed_in.get(reverse("fantasy:roster-build", args=[roster.pk])).content.decode()
    assert "sort=return" in body
    assert '<option value="return"' in body
    assert body.count(f'datetime="{days(10).isoformat()}"') == 2  # table cell and card


# --- formatting ----------------------------------------------------------------------------


def test_days_until_reads_naturally():
    assert [days_until(n) for n in (0, 1, -1, 3, -4, None)] == [
        "today",
        "tomorrow",
        "yesterday",
        "in 3 days",
        "4 days ago",
        "",
    ]


def test_the_short_date_follows_the_language():
    date = dt.date(2026, 11, 2)
    assert short_month_day(date) == "2 Nov"
    with translation.override("de"):
        assert short_month_day(date) == "2. Nov"
        assert days_until(3) == "in 3 Tagen"
