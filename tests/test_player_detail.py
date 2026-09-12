"""The weekly snapshot history and the player detail page that reads it."""

import datetime as dt
import re
from decimal import Decimal
from itertools import pairwise

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.fantasy import services
from apps.fantasy.models import PlayerSnapshot, Season
from apps.nba.management.commands.seed_demo_data import (
    expand_to_schedule,
    fabricate_history,
    snapshot_schedule,
    snapshot_sundays,
)
from apps.nba.models import Player, Team


@pytest.fixture
def teams(db):
    call_command("seed_teams", verbosity=0)
    return {team.abbreviation: team for team in Team.objects.all()}


@pytest.fixture
def season(db):
    return Season.objects.create(
        label="2026-27",
        starts_on=dt.date(2026, 10, 20),  # a Tuesday
        ends_on=dt.date(2027, 4, 11),
        is_current=True,
    )


@pytest.fixture
def signed_in(client, user, password, db):
    client.login(email=user.email, password=password)
    return client


@pytest.fixture
def player(teams, season):
    """One player with three weekly snapshots: dearer, then cheaper again."""
    player = Player.objects.create(
        first_name="Test", last_name="History", position=Player.Position.GUARD, team=teams["LAL"]
    )
    sundays = snapshot_sundays(season, 3)
    figures = [
        (Decimal("2000000"), Decimal("60.00"), 3),
        (Decimal("2500000"), Decimal("150.00"), 6),
        (Decimal("2200000"), Decimal("180.00"), 9),
    ]
    for as_of, (salary, total_fp, games) in zip(sundays, figures, strict=True):
        services.record_snapshot(
            player,
            season,
            as_of,
            salary=salary,
            total_fp=total_fp,
            games_played=games,
            team=player.team,
            position=player.position,
        )
    player.refresh_from_db()
    return player


def url(player):
    return reverse("nba:player-detail", args=[player.slug])


def chart_svg(body):
    """Just the chart. Not the first <svg> on the page -- that one is the
    favicon -- and not the table below it, which repeats the same figures."""
    svg = "<svg" + next(chunk for chunk in body.split("<svg") if "<polyline" in chunk)
    return svg[: svg.index("</svg>") + 6]


# --- the simulated Sundays ----------------------------------------------------


def test_the_snapshots_land_on_the_seasons_first_sundays(season):
    sundays = snapshot_sundays(season, 6)
    assert len(sundays) == 6
    # The season tips off on Tuesday 20.10., so the first snapshot is the 25th.
    assert [day.date() for day in sundays] == [
        dt.date(2026, 10, 25),
        dt.date(2026, 11, 1),
        dt.date(2026, 11, 8),
        dt.date(2026, 11, 15),
        dt.date(2026, 11, 22),
        dt.date(2026, 11, 29),
    ]
    assert all(day.weekday() == 6 for day in sundays)


def test_a_season_starting_on_a_sunday_starts_there(db):
    season = Season.objects.create(
        label="Sonntagssaison", starts_on=dt.date(2026, 10, 25), ends_on=dt.date(2027, 4, 11)
    )
    assert snapshot_sundays(season, 1)[0].date() == dt.date(2026, 10, 25)


def test_the_snapshots_are_a_week_apart(season):
    gaps = {later - earlier for earlier, later in pairwise(snapshot_sundays(season, 6))}
    assert gaps == {dt.timedelta(weeks=1)}


# --- the scrapes that are not on a Sunday -------------------------------------


def test_the_schedule_keeps_every_sunday(season):
    schedule = snapshot_schedule(season, 6)
    assert set(snapshot_sundays(season, 6)) <= set(schedule)
    assert schedule == sorted(schedule)


def test_the_schedule_has_scrapes_between_the_sundays(season):
    """A perfectly weekly history never exercises the screens that must cope."""
    schedule = snapshot_schedule(season, 6)
    assert len(schedule) > 6
    assert any(when.weekday() != 6 for when in schedule)


def test_one_sunday_is_scraped_twice(season):
    """What a retry after a timeout looks like from the outside."""
    schedule = snapshot_schedule(season, 6)
    days = [when.date() for when in schedule]
    twice = [day for day in set(days) if days.count(day) == 2]
    assert len(twice) == 1
    first, second = sorted(when for when in schedule if when.date() == twice[0])
    assert second - first < dt.timedelta(hours=1)


def test_the_series_still_ends_on_its_last_sunday(season):
    """Every current price is derived from that snapshot, so nothing may follow it."""
    sundays = snapshot_sundays(season, 6)
    assert max(snapshot_schedule(season, 6)) == sundays[-1]
    assert min(snapshot_schedule(season, 6)) == sundays[0]


def test_a_one_week_season_has_nowhere_to_put_them(season):
    """Off-cadence scrapes are interpolated between two Sundays, and one Sunday
    is not two."""
    assert snapshot_schedule(season, 1) == snapshot_sundays(season, 1)


def test_the_sundays_survive_the_expansion_untouched(season):
    """The interpolated rows are added to the weekly series, not derived instead
    of it -- so no existing screen moves."""
    import random

    sundays = snapshot_sundays(season, 6)
    weekly = fabricate_history(random.Random(11), Decimal("6000000"), 6)
    rows = expand_to_schedule(weekly, sundays, snapshot_schedule(season, 6))
    on_sundays = {when: (games, points, salary) for when, games, points, salary in rows}
    assert [on_sundays[sunday] for sunday in sundays] == weekly


def test_the_expanded_series_still_only_grows(season):
    import random

    sundays = snapshot_sundays(season, 6)
    for seed in range(15):
        weekly = fabricate_history(random.Random(seed), Decimal("3000000"), 6)
        rows = expand_to_schedule(weekly, sundays, snapshot_schedule(season, 6))
        assert [games for _, games, _, _ in rows] == sorted(games for _, games, _, _ in rows)
        assert [points for _, _, points, _ in rows] == sorted(points for _, _, points, _ in rows)


def test_a_midweek_scrape_still_reads_the_sundays_price(season):
    """The official game reprices once a week, so a Wednesday price is Sunday's."""
    import random

    sundays = snapshot_sundays(season, 6)
    weekly = fabricate_history(random.Random(4), Decimal("5000000"), 6)
    rows = expand_to_schedule(weekly, sundays, snapshot_schedule(season, 6))
    salaries = {when: salary for when, _, _, salary in rows}
    for when, salary in salaries.items():
        preceding = max(sunday for sunday in sundays if sunday <= when)
        assert salary == salaries[preceding]


def test_a_scrape_minutes_after_another_repeats_it_exactly(season):
    """Nothing happened in between, so nothing may appear to have."""
    import random

    sundays = snapshot_sundays(season, 6)
    schedule = snapshot_schedule(season, 6)
    rows = expand_to_schedule(
        fabricate_history(random.Random(5), Decimal("8000000"), 6), sundays, schedule
    )
    by_day = {}
    for when, *figures in rows:
        by_day.setdefault(when.date(), []).append(figures)
    doubled = next(figures for figures in by_day.values() if len(figures) == 2)
    assert doubled[0] == doubled[1]


# --- the fabricated history ---------------------------------------------------


def test_the_history_ends_on_the_price_we_already_show():
    """The series is built backwards, so no existing screen moves."""
    import random

    history = fabricate_history(random.Random(1), Decimal("7000000"), 6)
    assert len(history) == 6
    assert history[-1][2] == Decimal("7000000")


def test_points_and_games_only_ever_grow():
    """They are season totals. A total that falls is a scraping bug, not a slump."""
    import random

    history = fabricate_history(random.Random(7), Decimal("4000000"), 6)
    games = [games for games, _, _ in history]
    points = [points for _, points, _ in history]
    assert games == sorted(games)
    assert points == sorted(points)
    assert games[-1] >= 6  # at least a game a week, rest weeks aside


def test_prices_stay_above_the_floor():
    """Cheap players plus six weeks of bad form must not reach 0 $."""
    import random

    for seed in range(20):
        history = fabricate_history(random.Random(seed), Decimal("500000"), 6)
        assert all(salary >= Decimal("300000") for _, _, salary in history)


# --- the seed command ---------------------------------------------------------


def test_the_command_writes_one_snapshot_per_player_and_scrape(teams, season, settings):
    settings.DEBUG = True
    call_command("seed_demo_data", verbosity=0)
    players = Player.objects.count()
    scrapes = len(snapshot_schedule(season, 6))
    assert scrapes > 6
    assert PlayerSnapshot.objects.count() == players * scrapes
    # One scrape covers the whole pool, so every player shares the same moments.
    assert PlayerSnapshot.objects.values("as_of").distinct().count() == scrapes


def test_rerunning_the_command_neither_duplicates_nor_moves_prices(teams, season, settings):
    settings.DEBUG = True
    call_command("seed_demo_data", verbosity=0)
    before = PlayerSnapshot.objects.count()
    prices = dict(Player.objects.values_list("slug", "current_salary"))

    call_command("seed_demo_data", verbosity=0)
    assert PlayerSnapshot.objects.count() == before
    assert dict(Player.objects.values_list("slug", "current_salary")) == prices


def test_snapshots_off_the_commands_schedule_are_cleared_out(teams, season, settings):
    """Leftovers from an earlier shape of this data would read as an import bug."""
    settings.DEBUG = True
    player = Player.objects.create(
        first_name="Alt", last_name="Bestand", position=Player.Position.CENTER, team=teams["BOS"]
    )
    services.record_snapshot(
        player,
        season,
        timezone.now(),
        salary=Decimal("1000000"),
        total_fp=Decimal("10.00"),
        games_played=1,
        team=player.team,
    )
    call_command("seed_demo_data", verbosity=0)
    assert not player.snapshots.exists()


# --- the page -----------------------------------------------------------------


def test_the_detail_page_requires_login(client, player):
    response = client.get(url(player))
    assert response.status_code == 302
    assert reverse("accounts:login") in response.url


def test_an_unknown_slug_is_a_404(signed_in, db):
    assert signed_in.get(reverse("nba:player-detail", args=["gibt-es-nicht"])).status_code == 404


def test_the_page_names_the_player_and_his_team(signed_in, player):
    body = signed_in.get(url(player)).content.decode()
    assert "Test History" in body
    assert "Los Angeles Lakers" in body


def test_the_latest_snapshot_is_on_top(signed_in, player):
    rows = signed_in.get(url(player)).context["rows"]
    dates = [row["snapshot"].as_of.date() for row in rows]
    assert dates == sorted(dates, reverse=True)
    assert dates[0] == dt.date(2026, 11, 8)


def test_two_scrapes_on_one_day_are_told_apart_by_their_time(signed_in, player, season):
    """A retry minutes later is a second row with the same date and the same
    figures, so the only thing that distinguishes it is the clock."""
    sundays = snapshot_sundays(season, 3)
    services.record_snapshot(
        player,
        season,
        sundays[1] + dt.timedelta(minutes=41),
        salary=Decimal("2500000"),
        total_fp=Decimal("150.00"),
        games_played=6,
        team=player.team,
    )
    body = signed_in.get(url(player)).content.decode()
    assert body.count("<tr") == 5  # the header plus four scrapes
    assert "08:00" in body
    assert "08:41" in body


def test_every_snapshot_gets_a_row(signed_in, player):
    assert len(signed_in.get(url(player)).context["rows"]) == 3


def test_each_row_carries_the_change_to_the_week_before(signed_in, player):
    rows = signed_in.get(url(player)).context["rows"]
    newest = rows[0]  # 2,2m and 180 points, from 2,5m and 150
    assert newest["salary_change"] == Decimal("-300000.00")
    assert newest["total_fp_change"] == Decimal("30.00")
    assert newest["games_played_change"] == 3
    # 20.00 per game, down from 25.00.
    assert newest["fp_per_game_change"] == Decimal("-5.00")


def test_the_oldest_row_has_nothing_to_compare_against(signed_in, player):
    """None, not zero: there is no week before it in which nothing happened."""
    oldest = signed_in.get(url(player)).context["rows"][-1]
    assert oldest["salary_change"] is None
    assert oldest["total_fp_change"] is None
    assert oldest["fp_per_game_change"] is None


def test_the_change_since_the_first_snapshot_is_the_headline(signed_in, player):
    response = signed_in.get(url(player))
    assert response.context["salary_since_first"] == Decimal("200000.00")  # 2,0m -> 2,2m
    assert response.context["first_as_of"].date() == dt.date(2026, 10, 25)


def test_a_single_snapshot_claims_no_change_at_all(signed_in, teams, season):
    player = Player.objects.create(
        first_name="Test", last_name="Neuling", position=Player.Position.CENTER, team=teams["BOS"]
    )
    services.record_snapshot(
        player,
        season,
        snapshot_sundays(season, 1)[0],
        salary=Decimal("900000"),
        total_fp=Decimal("0.00"),
        games_played=0,
        team=player.team,
    )
    response = signed_in.get(url(player))
    assert "salary_since_first" not in response.context
    assert len(response.context["rows"]) == 1


def test_the_figures_are_rendered_in_millions_and_with_signs(signed_in, player):
    body = signed_in.get(url(player)).content.decode()
    assert "$2.20M" in body  # the newest salary
    assert "-$0.30M" in body  # what it fell by
    assert "+30.00" in body  # points gained that week
    assert 'title="$2,200,000"' in body
    assert "Hotness" in body
    assert "1/2" in body


def test_no_template_comment_leaks_into_the_page(signed_in, player):
    """A `{# ... #}` comment only works on one line; a wrapped one renders as text."""
    assert "{#" not in signed_in.get(url(player)).content.decode()


def test_a_player_without_snapshots_says_so(signed_in, teams):
    player = Player.objects.create(
        first_name="No",
        last_name="Snapshot",
        position=Player.Position.GUARD,
        team=teams["BOS"],
    )
    body = signed_in.get(url(player)).content.decode()
    assert "No snapshots on record" in body


def test_player_detail_shows_healthy_without_injury_history(signed_in, player):
    body = signed_in.get(url(player)).content.decode()

    assert "Healthy" in body
    assert "No injury report has been recorded" in body


def test_player_detail_shows_latest_injury_report(signed_in, player):
    from apps.nba.models import PlayerInjury

    PlayerInjury.objects.create(
        player=player,
        observed_at=timezone.now(),
        status="Out",
        injury_type="Knee",
        short_comment="Soreness",
    )
    body = signed_in.get(url(player)).content.decode()

    assert "Injured" in body
    assert "Latest injury report" in body
    assert "Soreness" in body
    assert "$0.00M" not in body


def test_an_inactive_player_keeps_his_history(signed_in, player):
    """A history page that stops resolving when someone leaves is not a history."""
    Player.objects.filter(pk=player.pk).update(is_active=False)
    response = signed_in.get(url(player))
    assert response.status_code == 200
    assert len(response.context["rows"]) == 3
    assert "Inactive" in response.content.decode()


def test_the_snapshot_shows_the_team_of_that_week(signed_in, player, teams, season):
    """A mid-season trade must stay visible in the weeks before it."""
    services.record_snapshot(
        player,
        season,
        snapshot_sundays(season, 4)[3],
        salary=Decimal("2200000"),
        total_fp=Decimal("200.00"),
        games_played=11,
        team=teams["BOS"],
        position=player.position,
    )
    rows = signed_in.get(url(player)).context["rows"]
    assert [row["snapshot"].team.abbreviation for row in rows] == ["BOS", "LAL", "LAL", "LAL"]
    player.refresh_from_db()
    assert player.team == teams["BOS"]


# --- the chart ----------------------------------------------------------------


def test_the_chart_plots_every_sunday_oldest_first(signed_in, player):
    """A line is read forwards through the season; the table below it is not."""
    chart = signed_in.get(url(player)).context["fp_chart"]
    assert [dot["value"] for dot in chart["dots"]] == [
        Decimal("20.00"),
        Decimal("25.00"),
        Decimal("20.00"),
    ]
    assert [label["label"] for label in chart["x_labels"]] == ["25.10.", "01.11.", "08.11."]


def test_the_chart_is_drawn_into_the_page(signed_in, player):
    body = signed_in.get(url(player)).content.decode()
    assert "<polyline" in body
    assert "Points per game across the season" in body
    # Reachable without sight and without JavaScript.
    assert 'aria-label="Points per game for Test History"' in body


def test_no_coordinate_is_rendered_with_a_decimal_comma(signed_in, player):
    """`189,91` is a coordinate no browser draws.

    The geometry goes through `|unlocalize` so it stays machine-readable whatever
    the locale is set to. Under en-us nothing would put a comma there anyway --
    this is the guard that catches the day someone switches the locale back.
    """
    body = signed_in.get(url(player)).content.decode()
    svg = chart_svg(body)
    assert re.search(r'(?:x|y|x1|y1|x2|y2|cx|cy)="[^"]*,', svg) is None
    # The numbers a person reads are localised.
    assert ">24<" in svg
    assert ">25.00" in svg


def test_every_point_shows_its_value_on_hover(signed_in, player):
    """The whole ask: hover a marker, read the points per game at that Sunday."""
    body = signed_in.get(url(player)).content.decode()
    svg = chart_svg(body)
    # One readout per point, revealed by CSS rather than by script.
    assert svg.count("chart-readout") == 3
    assert svg.count(">25.00<") == 1  # the middle Sunday's figure, as text
    # And a band per point to hover in, wider than the dot it belongs to.
    assert svg.count("chart-hit") == 3
    # The readout has to be a later sibling of the band that reveals it.
    assert svg.index("chart-hit") < svg.index("chart-readout")


def test_the_chart_sits_above_the_table(signed_in, player):
    body = signed_in.get(url(player)).content.decode()
    assert body.index("<polyline") < body.index("<table")


def test_a_single_sunday_gets_a_sentence_instead_of_a_line(signed_in, teams, season):
    player = Player.objects.create(
        first_name="Test", last_name="Einmalig", position=Player.Position.GUARD, team=teams["BOS"]
    )
    services.record_snapshot(
        player,
        season,
        snapshot_sundays(season, 1)[0],
        salary=Decimal("1000000"),
        total_fp=Decimal("40.00"),
        games_played=2,
        team=player.team,
    )
    response = signed_in.get(url(player))
    assert response.context["fp_chart"] is None
    assert "at least two snapshots" in response.content.decode()


def test_weeks_before_the_first_game_are_not_plotted_as_zero(signed_in, teams, season):
    """No games is a gap in the measurement, not a fortnight of scoring nothing."""
    player = Player.objects.create(
        first_name="Test", last_name="Verletzt", position=Player.Position.CENTER, team=teams["BOS"]
    )
    sundays = snapshot_sundays(season, 4)
    figures = [
        (Decimal("0.00"), 0),
        (Decimal("0.00"), 0),
        (Decimal("30.00"), 2),
        (Decimal("75.00"), 5),
    ]
    for as_of, (total_fp, games) in zip(sundays, figures, strict=True):
        services.record_snapshot(
            player,
            season,
            as_of,
            salary=Decimal("1000000"),
            total_fp=total_fp,
            games_played=games,
            team=player.team,
        )
    chart = signed_in.get(url(player)).context["fp_chart"]
    assert [dot["value"] for dot in chart["dots"]] == [Decimal("15.00"), Decimal("15.00")]
    # All four Sundays still appear on the axis: the season did not get shorter.
    assert len(chart["x_labels"]) == 4
    assert chart["low"] > 0


# --- getting there ------------------------------------------------------------


def test_the_player_list_links_to_the_detail_page(signed_in, player):
    body = signed_in.get(reverse("nba:player-list")).content.decode()
    assert f'href="{url(player)}"' in body


def test_the_team_page_links_to_the_detail_page(signed_in, player):
    body = signed_in.get(reverse("nba:team-detail", args=["lal"])).content.decode()
    assert f'href="{url(player)}"' in body
