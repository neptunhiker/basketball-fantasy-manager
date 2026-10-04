"""The first round of UX fixes from the app review, one section per issue."""

import datetime as dt
import re
from decimal import Decimal
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.fantasy import services
from apps.fantasy.models import Manager, Roster, Season
from apps.nba.bbde import BbdePage
from apps.nba.importer import NoTeams, import_bbde
from apps.nba.models import ImportRun, Player, Team

User = get_user_model()


@pytest.fixture
def signed_in(client, user, password):
    client.login(email=user.email, password=password)
    return client


@pytest.fixture
def live_season(db):
    today = timezone.localdate()
    return Season.objects.create(
        label="Live",
        starts_on=today - dt.timedelta(days=30),
        ends_on=today + dt.timedelta(days=150),
        is_current=True,
    )


@pytest.fixture
def roster(manager, live_season):
    return Roster.objects.create(manager=manager, season=live_season, name="Roster 1")


def player(last_name, position="G", salary=1_000_000, total_fp=None, games=None):
    return Player.objects.create(
        first_name="P",
        last_name=last_name,
        position=position,
        current_salary=salary,
        current_total_fp=total_fp,
        current_games_played=games,
        current_fp_per_game=(Decimal(total_fp) / games) if total_fp and games else None,
    )


def names_in_order(body, *names):
    return sorted(names, key=body.index)


# --- 1. filtering on the watchlist stays on the watchlist ----------------------------


def test_the_watchlist_filter_reloads_the_watchlist(signed_in):
    body = signed_in.get(reverse("nba:watchlist")).text

    assert f'hx-get="{reverse("nba:watchlist")}"' in body


def test_a_filtered_watchlist_shows_only_watched_players(signed_in, user):
    watched = player("Watched")
    player("Other")
    services.add_to_watchlist(user, watched)

    body = signed_in.get(
        reverse("nba:watchlist"), {"position": "G"}, headers={"HX-Request": "true"}
    ).text

    assert "P Watched" in body
    assert "P Other" not in body


# --- 2. numbers sort high-first ---------------------------------------------------------


def test_the_player_list_opens_with_the_best_scorers_first(signed_in):
    player("Low", total_fp=10, games=10)
    player("High", total_fp=300, games=10)
    player("Unplayed")

    body = signed_in.get(reverse("nba:player-list")).text

    assert names_in_order(body, "P High", "P Low", "P Unplayed") == [
        "P High",
        "P Low",
        "P Unplayed",
    ]


def test_the_first_click_on_a_number_column_sorts_high_first(signed_in):
    player("Anyone")  # the table, and its headers, only render with players
    body = signed_in.get(reverse("nba:player-list")).text

    salary_link = re.search(r'href="([^"]*sort=salary[^"]*)"', body).group(1)
    name_link = re.search(r'href="([^"]*sort=name[^"]*)"', body).group(1)
    assert "dir=desc" in salary_link
    assert "dir=asc" in name_link


def test_the_active_column_still_flips_on_a_second_click(signed_in):
    player("Anyone")
    body = signed_in.get(reverse("nba:player-list"), {"sort": "salary", "dir": "desc"}).text

    salary_link = re.search(r'href="([^"]*sort=salary[^"]*)"', body).group(1)
    assert "dir=asc" in salary_link


# --- 3. the bar above the market ---------------------------------------------------------


def test_the_market_bar_shows_positions_cash_and_budget_per_open_slot(signed_in, roster):
    services.buy(roster, player("Guard", "G", 7_000_000), Decimal("7000000"))

    body = signed_in.get(reverse("fantasy:roster-build", args=[roster.pk])).text
    bar = body[body.index('id="player-picker"') : body.index('id="picker-filters"')]

    assert "1/5" in bar  # guards
    assert "0/2" in bar  # centers
    assert "$56.00M" in bar  # cash: 63M - 7M
    assert "Avg. per open slot (14 open)" in bar
    assert "$4.00M" in bar  # 56M / 14


def test_a_full_roster_has_no_per_slot_figure(signed_in, roster):
    Roster.objects.filter(pk=roster.pk).update(cash=Decimal("15000000"))
    for n in range(15):
        services.buy(roster, player(f"N{n}", "GFC"[n % 3], 1_000_000), Decimal("0"))

    body = signed_in.get(reverse("fantasy:roster-build", args=[roster.pk])).text

    assert "Avg. per open slot" not in body


# --- 4. the trade button and the 0-trades note ------------------------------------------


def test_the_trade_button_is_enabled_with_no_trades_left(signed_in, roster):
    body = signed_in.get(reverse("fantasy:roster-build", args=[roster.pk])).text
    header = body[: body.index('id="roster-panel"')]

    assert roster.trades_available == 0
    assert f'hx-get="{reverse("fantasy:roster-trade", args=[roster.pk])}"' in header
    assert "No trades available" not in header


def test_the_trade_dialog_says_when_the_next_free_trade_arrives(signed_in, roster, live_season):
    live_season.trade_grants.create(
        label="Helpside Trade", granted_at=timezone.now() + dt.timedelta(days=5), trades=1
    )

    body = signed_in.get(reverse("fantasy:roster-trade", args=[roster.pk])).text

    assert "No trades available for this roster." in body
    assert "Next free trade: Helpside Trade" in body
    assert reverse("fantasy:roster-rules", args=[roster.pk]) in body


def test_the_trade_dialog_offers_buying_when_the_market_is_open(signed_in, roster):
    body = signed_in.get(reverse("fantasy:roster-trade", args=[roster.pk])).text

    assert "buy one for $1.50M" in body


# --- 6. no current season ----------------------------------------------------------------


def test_without_a_season_players_are_pointed_at_the_player_list(signed_in, manager):
    response = signed_in.get(reverse("fantasy:roster-create"))

    body = response.content.decode()
    assert "An administrator still needs to start the season." in body
    assert reverse("nba:player-list") in body
    assert reverse("fantasy:season-list") not in body


def test_without_a_season_admins_are_sent_to_the_seasons(client, password, db):
    admin = User.objects.create_superuser(email="boss@example.com", password=password)
    Manager.objects.create(user=admin, nick_name="Boss")
    client.login(email=admin.email, password=password)

    body = client.get(reverse("fantasy:roster-create")).content.decode()

    assert reverse("fantasy:season-list") in body


# --- 7. teams exist, and the import will not run without them ----------------------------


def test_every_database_has_the_thirty_teams(db):
    assert Team.objects.count() == 30
    assert Team.objects.get(abbreviation="LAC").name == "LA Clippers"


def test_the_import_refuses_to_start_without_teams(live_season):
    Team.objects.all().delete()
    client = mock.Mock()
    client.iter_pages.return_value = [BbdePage()]

    with pytest.raises(NoTeams):
        import_bbde("coach", "s3cret", client=client)

    client.login.assert_not_called()
    run = ImportRun.objects.get()
    assert run.status == ImportRun.Status.FAILED
    assert "seed_teams" in run.error


def test_the_import_page_explains_missing_teams(client, staff_user, password, live_season):
    Team.objects.all().delete()
    client.login(email=staff_user.email, password=password)

    body = client.post(reverse("nba:bbde-import"), {"username": "coach", "password": "s3cret"}).text

    assert "There are no NBA teams in the database yet." in body
