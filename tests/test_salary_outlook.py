"""The next Sunday salary update: the baseline, the pick premium, the prediction."""

import datetime as dt
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.fantasy import services
from apps.fantasy.models import Season
from apps.nba.models import MILLION, Player, Team, expected_salary_expression, predict_salary
from apps.nba.salary_outlook import (
    AFTER_UPDATE,
    PRESEASON,
    average_error,
    past_updates,
    salary_baseline,
    salary_outlook,
    with_salary_outlook,
)

pytestmark = pytest.mark.django_db


def local(year, month, day, hour=0, minute=0):
    return timezone.make_aware(dt.datetime(year, month, day, hour, minute))


def poly(fp_per_game):
    """The expected salary in dollars."""
    return predict_salary(Decimal(str(fp_per_game))) * MILLION


# The 2026-27 calendar: the season starts on 21 October, salaries first move
# when trade buying opens on Sunday 15 November at 22:00.
FIRST_UPDATE = local(2026, 11, 15, 22)
SECOND_UPDATE = local(2026, 11, 22, 22)


@pytest.fixture
def season(db):
    return Season.objects.create(
        label="2026-27",
        starts_on=dt.date(2026, 10, 21),
        ends_on=dt.date(2027, 4, 11),
        is_current=True,
        trade_market_opens_at=FIRST_UPDATE,
    )


def make_player(last_name="Case"):
    return Player.objects.create(
        first_name="P", last_name=last_name, position="G", team=Team.objects.get(abbreviation="LAL")
    )


def read(player, season, when, salary, fp_per_game, games):
    """One snapshot with a given average: total points are average times games."""
    services.record_snapshot(
        player,
        season,
        when,
        salary=salary,
        total_fp=Decimal(str(fp_per_game)) * games,
        games_played=games,
        team=player.team,
        position=player.position,
    )
    player.refresh_from_db()
    return player


def annotated(player, baseline):
    return with_salary_outlook(Player.objects.filter(pk=player.pk), baseline).get()


# --- the Sunday updates ---------------------------------------------------------


def test_updates_are_every_sunday_from_trade_buying_on(season):
    moments = season.salary_update_moments(until=local(2026, 12, 6, 23))

    assert moments == [
        FIRST_UPDATE,
        SECOND_UPDATE,
        local(2026, 11, 29, 22),
        local(2026, 12, 6, 22),
    ]


def test_updates_stay_at_22_00_across_the_clock_change(season):
    # Summer time ends on 25 October 2026.
    season.trade_market_opens_at = local(2026, 10, 18, 22)

    moments = season.salary_update_moments(until=local(2026, 11, 1, 23))

    assert [timezone.localtime(moment).hour for moment in moments] == [22, 22, 22]
    # An hour longer than a week in real time: the clocks went back.
    utc = [moment.astimezone(dt.UTC) for moment in moments]
    assert utc[1] - utc[0] == dt.timedelta(days=7, hours=1)


def test_no_updates_without_a_trade_buying_date(season):
    season.trade_market_opens_at = None

    assert season.salary_update_moments() == []


# --- which snapshot a prediction starts from -------------------------------------


def test_no_baseline_without_a_season(db):
    assert salary_baseline() is None


def test_before_the_first_update_the_baseline_is_the_season_start(season):
    read(make_player(), season, local(2026, 10, 13, 20), 4 * MILLION, 0, 0)

    baseline = salary_baseline(now=local(2026, 11, 1, 12))

    assert baseline.kind == PRESEASON
    assert baseline.moment == season.season_open_at
    assert baseline.next_update == FIRST_UPDATE


def test_an_update_counts_once_a_snapshot_was_read_after_it(season):
    player = make_player()
    read(player, season, local(2026, 11, 15, 12, 15), 4 * MILLION, 20, 10)

    # 22:05: repriced, but the 22:15 import has not brought the new prices yet.
    assert salary_baseline(now=local(2026, 11, 15, 22, 5)).kind == PRESEASON

    read(player, season, local(2026, 11, 15, 22, 15), 5 * MILLION, 20, 10)
    baseline = salary_baseline(now=local(2026, 11, 15, 22, 20))

    assert baseline.kind == AFTER_UPDATE
    assert baseline.moment == FIRST_UPDATE
    assert baseline.next_update == SECOND_UPDATE


def test_the_newest_update_read_is_the_baseline(season):
    player = make_player()
    read(player, season, local(2026, 11, 15, 22, 15), 5 * MILLION, 20, 10)
    read(player, season, local(2026, 11, 22, 22, 15), 6 * MILLION, 22, 12)

    baseline = salary_baseline(now=local(2026, 11, 25, 12))

    assert baseline.moment == SECOND_UPDATE
    assert baseline.next_update == local(2026, 11, 29, 22)


# --- the prediction ---------------------------------------------------------------

PREMIUM = Decimal("600000")


def picked_player(season, fp_now):
    """The worked example: priced 0.6M above his 20 FP/G at the first update."""
    player = make_player()
    read(player, season, local(2026, 10, 13, 20), 4 * MILLION, 0, 0)
    read(player, season, local(2026, 11, 15, 12, 15), 4 * MILLION, 20, 10)
    read(player, season, local(2026, 11, 15, 22, 15), poly(20) + PREMIUM, 20, 10)
    return read(player, season, local(2026, 11, 18, 12, 15), poly(20) + PREMIUM, fp_now, 12)


def test_the_premium_carries_over_when_his_average_holds(season):
    player = picked_player(season, fp_now=20)

    outlook = salary_outlook(player, salary_baseline(now=local(2026, 11, 19)))

    assert outlook.premium == PREMIUM
    assert outlook.salary == poly(20) + PREMIUM
    assert outlook.change == 0


def test_a_better_average_adds_its_expected_salary_on_top_of_the_premium(season):
    player = picked_player(season, fp_now=25)

    outlook = salary_outlook(player, salary_baseline(now=local(2026, 11, 19)))

    assert outlook.expected == poly(25)
    assert outlook.salary == poly(25) + PREMIUM
    assert outlook.change == poly(25) - poly(20)
    assert outlook.baseline.as_of == local(2026, 11, 15, 22, 15)


def test_before_the_first_update_there_is_no_premium(season):
    player = make_player()
    read(player, season, local(2026, 10, 13, 20), 4 * MILLION, 0, 0)
    read(player, season, local(2026, 11, 1, 12, 15), 4 * MILLION, 20, 8)

    outlook = salary_outlook(player, salary_baseline(now=local(2026, 11, 2)))

    assert outlook.premium == 0
    assert outlook.salary == poly(20)
    assert outlook.change == poly(20) - 4 * MILLION
    assert outlook.baseline.as_of == local(2026, 10, 13, 20)


def test_the_last_snapshot_before_the_season_is_the_preseason_baseline(season):
    player = make_player()
    read(player, season, local(2026, 10, 6, 20), 3 * MILLION, 0, 0)
    read(player, season, local(2026, 10, 13, 20), 4 * MILLION, 0, 0)
    read(player, season, local(2026, 10, 22, 12), 4 * MILLION, 20, 1)

    outlook = salary_outlook(player, salary_baseline(now=local(2026, 10, 23)))

    assert outlook.baseline.as_of == local(2026, 10, 13, 20)


def test_no_prediction_without_games_at_the_update(season):
    player = make_player()
    read(player, season, local(2026, 11, 15, 22, 15), 1 * MILLION, 0, 0)
    read(player, season, local(2026, 11, 18, 12), 1 * MILLION, 15, 1)

    assert salary_outlook(player, salary_baseline(now=local(2026, 11, 19))) is None


def test_no_prediction_for_a_player_first_listed_after_the_update(season):
    other = make_player("Other")
    read(other, season, local(2026, 11, 15, 22, 15), 1 * MILLION, 10, 5)
    newcomer = make_player()
    read(newcomer, season, local(2026, 11, 14, 12), 1 * MILLION, 10, 5)

    # His only snapshot is older than the update the others were read after.
    assert salary_outlook(newcomer, salary_baseline(now=local(2026, 11, 19))) is None


def test_a_large_negative_premium_bottoms_out_at_the_minimum_salary(season):
    player = make_player()
    read(player, season, local(2026, 11, 15, 22, 15), poly(10) - 3 * MILLION, 10, 5)
    read(player, season, local(2026, 11, 18, 12), poly(10) - 3 * MILLION, 5, 6)

    outlook = salary_outlook(player, salary_baseline(now=local(2026, 11, 19)))

    assert outlook.salary == Decimal("500000")


# --- Python and the database agree ---------------------------------------------


@pytest.mark.parametrize("fp_now", [20, 25, 13.37, 4.5, None])
def test_python_and_database_agree_after_an_update(season, fp_now):
    player = make_player()
    read(player, season, local(2026, 11, 15, 22, 15), poly(20) + PREMIUM, 20, 10)
    if fp_now is not None:
        read(player, season, local(2026, 11, 18, 12), poly(20) + PREMIUM, fp_now, 12)
    baseline = salary_baseline(now=local(2026, 11, 19))

    outlook = salary_outlook(player, baseline)
    row = annotated(player, baseline)

    if fp_now is None:
        # The newest snapshot is the update itself: nothing moved yet.
        assert row.outlook_change == outlook.change == 0
    else:
        assert row.pick_premium == outlook.premium
        assert row.outlook_salary == outlook.salary
        assert row.outlook_change == outlook.change


def test_python_and_database_agree_before_the_first_update(season):
    player = make_player()
    read(player, season, local(2026, 10, 13, 20), 4 * MILLION, 0, 0)
    read(player, season, local(2026, 11, 1, 12), 4 * MILLION, 31.4, 8)
    baseline = salary_baseline(now=local(2026, 11, 2))

    outlook = salary_outlook(player, baseline)
    row = annotated(player, baseline)

    assert row.pick_premium == outlook.premium == 0
    assert row.outlook_change == outlook.change


def test_the_database_leaves_missing_figures_empty(season):
    player = make_player()
    read(player, season, local(2026, 11, 15, 22, 15), 1 * MILLION, 0, 0)
    row = annotated(player, salary_baseline(now=local(2026, 11, 19)))

    assert row.pick_premium is None
    assert row.outlook_salary is None
    assert row.outlook_change is None


def test_no_baseline_annotates_nothing(db):
    player = make_player()

    row = annotated(player, None)

    assert row.outlook_change is None


@pytest.mark.parametrize(
    "fp_per_game", ["0", "0.01", "3.10", "9.99", "17.25", "24.50", "33.33", "48.70", "-1.00"]
)
def test_the_expected_salary_expression_matches_predict_salary(fp_per_game):
    player = make_player()
    Player.objects.filter(pk=player.pk).update(current_fp_per_game=Decimal(fp_per_game))

    row = Player.objects.annotate(expected=expected_salary_expression("current_fp_per_game")).get(
        pk=player.pk
    )

    assert row.expected == poly(fp_per_game)


# --- the player list --------------------------------------------------------------


@pytest.fixture
def signed_in(client, user, password):
    client.login(email=user.email, password=password)
    return client


def three_players_after_an_update(season):
    """Risers in the order Riser, Holder, Faller, read a day after the first update."""
    for name, fp_now in (("Holder", 20), ("Riser", 30), ("Faller", 12)):
        player = make_player(name)
        read(player, season, local(2026, 11, 15, 22, 15), poly(20) + PREMIUM, 20, 10)
        read(player, season, local(2026, 11, 16, 12, 15), poly(20) + PREMIUM, fp_now, 12)


def names_in_order(response):
    return [player.last_name for player in response.context["players"]]


def pin_the_clock(monkeypatch, now):
    """The pages read the baseline at `now` -- only the baseline, so the login
    session does not expire under a clock moved weeks ahead."""
    monkeypatch.setattr("apps.nba.views.salary_baseline", lambda: salary_baseline(now=now))


@pytest.fixture
def after_the_first_update(season, monkeypatch):
    three_players_after_an_update(season)
    pin_the_clock(monkeypatch, local(2026, 11, 16, 18))
    return season


def test_the_list_sorts_by_the_predicted_change(signed_in, after_the_first_update):
    response = signed_in.get(reverse("nba:player-list"), {"sort": "outlook"})

    assert names_in_order(response) == ["Riser", "Holder", "Faller"]
    page = response.content.decode()
    assert "Next salary update" in page
    assert "Biggest risers" in page
    assert "Premium" in page


def test_the_list_sorts_by_the_pick_premium(signed_in, after_the_first_update):
    other = make_player("Bargain")
    read(other, after_the_first_update, local(2026, 11, 15, 22, 15), poly(20) - PREMIUM, 20, 10)

    response = signed_in.get(reverse("nba:player-list"), {"sort": "premium", "dir": "asc"})

    assert names_in_order(response)[0] == "Bargain"


def test_the_team_page_sorts_by_the_predicted_change(signed_in, after_the_first_update):
    response = signed_in.get(
        reverse("nba:team-detail", args=["lal"]), {"sort": "outlook", "dir": "asc"}
    )

    assert names_in_order(response) == ["Faller", "Holder", "Riser"]


def test_before_the_first_update_the_premium_column_stays_away(signed_in, season, monkeypatch):
    player = make_player()
    read(player, season, local(2026, 10, 13, 20), 4 * MILLION, 0, 0)
    read(player, season, local(2026, 11, 1, 12), 4 * MILLION, 20, 8)
    pin_the_clock(monkeypatch, local(2026, 11, 2))

    response = signed_in.get(reverse("nba:player-list"))

    page = response.content.decode()
    assert "No update yet this season" in page
    assert "What is Premium?" not in page
    assert "What is Next update?" in page


def test_without_a_season_the_list_has_no_prediction(signed_in, db):
    response = signed_in.get(reverse("nba:player-list"))

    assert response.status_code == 200
    assert "Next salary update" not in response.content.decode()


# --- the roster and the trade dialog ---------------------------------------------


@pytest.fixture
def roster(manager, after_the_first_update, monkeypatch):
    from apps.fantasy.models import Roster

    monkeypatch.setattr(
        "apps.fantasy.views.salary_baseline",
        lambda: salary_baseline(now=local(2026, 11, 16, 18)),
    )
    roster = Roster.objects.create(
        manager=manager, season=after_the_first_update, name="Bulla Ballers"
    )
    for player in Player.objects.filter(last_name__in=["Holder", "Riser"]):
        services.buy(roster, player, player.current_salary)
    return roster


def test_the_roster_sorts_by_the_predicted_change(signed_in, roster):
    response = signed_in.get(
        reverse("fantasy:roster-build", args=[roster.pk]), {"sort": "outlook", "dir": "desc"}
    )

    names = [membership.player.last_name for membership in response.context["memberships"]]
    assert names == ["Riser", "Holder"]
    assert response.context["squad_outlook"] == {"change": poly(30) - poly(20), "missing": 0}
    keys = [key for key, _label in response.context["squad_sort_options"]]
    assert "premium" in keys and "outlook" in keys


def test_the_trade_dialog_nets_both_players_changes(signed_in, roster):
    out = Player.objects.get(last_name="Holder")
    in_ = Player.objects.get(last_name="Faller")

    response = signed_in.get(
        reverse("fantasy:roster-trade", args=[roster.pk]), {"out": out.pk, "in": in_.pk}
    )

    outlook = response.context["trade_outlook"]
    assert outlook["out"] == 0
    assert outlook["in"] == poly(12) - poly(20)
    assert outlook["net"] == poly(12) - poly(20)
    available = {player.last_name: player for player in response.context["available"]}
    assert available["Faller"].outlook_change == poly(12) - poly(20)


# --- how the prediction fared at past updates -------------------------------------


def two_updates(season):
    """The worked example over two updates: 0.6M of picks on top of 20 FP/G at
    the first, then 25 FP/G and another 0.2M of picks at the second."""
    player = make_player()
    read(player, season, local(2026, 10, 13, 20), 4 * MILLION, 0, 0)
    read(player, season, local(2026, 11, 15, 12, 15), 4 * MILLION, 20, 10)
    read(player, season, local(2026, 11, 15, 22, 15), poly(20) + PREMIUM, 20, 10)
    read(player, season, local(2026, 11, 22, 12, 15), poly(20) + PREMIUM, 25, 12)
    read(player, season, local(2026, 11, 22, 22, 15), poly(25) + PREMIUM + 200_000, 25, 12)
    return player


def test_each_past_update_is_predicted_as_it_would_have_been(season):
    player = two_updates(season)

    rows = past_updates(list(player.snapshots.all()), salary_baseline(now=local(2026, 11, 24)))

    second, first = rows
    # The first update: no premium yet, so the polynomial alone.
    assert first.moment == FIRST_UPDATE
    assert first.predicted == poly(20)
    assert first.error == PREMIUM
    assert first.premium == PREMIUM
    assert first.change == poly(20) + PREMIUM - 4 * MILLION
    # The second: the first update's premium carried onto 25 FP/G.
    assert second.moment == SECOND_UPDATE
    assert second.predicted == poly(25) + PREMIUM
    assert second.error == 200_000
    assert second.premium == PREMIUM + 200_000
    assert average_error(rows) == {"error": (PREMIUM + 200_000) / 2, "count": 2}


def test_an_update_not_yet_read_is_not_a_past_update(season):
    player = two_updates(season)

    rows = past_updates(
        list(player.snapshots.all()), salary_baseline(now=local(2026, 11, 22, 22, 5))
    )

    assert [row.moment for row in rows] == [FIRST_UPDATE]


def test_a_week_without_a_snapshot_has_no_row(season):
    player = make_player()
    read(player, season, local(2026, 11, 15, 22, 15), poly(20), 20, 10)
    # Nothing read on 22 November.
    read(player, season, local(2026, 11, 29, 22, 15), poly(22), 22, 14)

    rows = past_updates(list(player.snapshots.all()), salary_baseline(now=local(2026, 11, 30)))

    assert [row.moment for row in rows] == [local(2026, 11, 29, 22), FIRST_UPDATE]


def test_no_past_updates_before_the_first(season):
    assert past_updates([], salary_baseline(now=local(2026, 11, 1))) == []


def test_no_average_without_a_prediction():
    assert average_error([]) is None


def test_the_player_page_shows_the_next_update_and_the_past_ones(signed_in, season, monkeypatch):
    player = two_updates(season)
    read(player, season, local(2026, 11, 24, 12), poly(25) + PREMIUM + 200_000, 30, 14)
    pin_the_clock(monkeypatch, local(2026, 11, 24, 18))

    response = signed_in.get(reverse("nba:player-detail", args=[player.slug]))

    outlook = response.context["salary_outlook"]
    assert outlook.premium == PREMIUM + 200_000
    assert outlook.change == poly(30) - poly(25)
    assert len(response.context["past_updates"]) == 2
    page = response.content.decode()
    assert "Expected change" in page
    assert "Past updates" in page
