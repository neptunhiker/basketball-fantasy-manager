"""The roster's Latest tab: what the newest import and the last day's news changed."""

import datetime as dt
from decimal import Decimal
from itertools import count

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.fantasy import services
from apps.fantasy.latest import roster_latest
from apps.fantasy.models import Roster, Season
from apps.nba.models import Game, Player, PlayerNews, Team

pytestmark = pytest.mark.django_db

_ids = count(1)


@pytest.fixture
def season(db):
    today = timezone.localdate()
    return Season.objects.create(
        label="2026-27",
        starts_on=today - dt.timedelta(days=30),
        ends_on=today + dt.timedelta(days=150),
        is_current=True,
    )


@pytest.fixture
def now():
    return timezone.now()


@pytest.fixture
def imports(now):
    """The two newest imports: yesterday's and today's, an hour ago."""
    return now - dt.timedelta(days=1, hours=1), now - dt.timedelta(hours=1)


@pytest.fixture
def roster(manager, season):
    return Roster.objects.create(manager=manager, season=season, name="Foxes")


def make_player(last_name, team="LAL", position="G"):
    return Player.objects.create(
        first_name="P",
        last_name=last_name,
        position=position,
        team=Team.objects.get(abbreviation=team),
    )


def snapshot(player, season, as_of, total_fp, games, salary=5_000_000):
    services.record_snapshot(
        player,
        season,
        as_of,
        salary=salary,
        total_fp=Decimal(str(total_fp)),
        games_played=games,
        team=player.team,
        position=player.position,
    )


def signed(roster, season, imports, last_name, before, after, team="LAL"):
    """A player on `roster` with one snapshot per import: `(fp, games, salary)` each."""
    player = make_player(last_name, team=team)
    for as_of, values in zip(imports, (before, after), strict=True):
        snapshot(player, season, as_of, *values)
    player.refresh_from_db()
    services.buy(roster, player, player.current_salary)
    return player


def news(player, hours_ago, now, kind=PlayerNews.Kind.NEWS, headline="Something happened"):
    return PlayerNews.objects.create(
        rotowire_news_id=next(_ids),
        player=player,
        kind=kind,
        headline=headline,
        url="https://www.rotowire.com/basketball/news.php",
        published_on=(now - dt.timedelta(hours=hours_ago)).date(),
        first_seen_at=now - dt.timedelta(hours=hours_ago),
        feed_player_name=player.full_name if player else "Somebody Else",
    )


def report_for(roster, now):
    return roster_latest(Roster.objects.get(pk=roster.pk), now=now)


def names(rows):
    return [row.player.last_name for row in rows]


def test_players_fall_into_four_groups(roster, season, imports, now):
    signed(roster, season, imports, "Scorer", (100, 4, 9_000_000), (150, 5, 9_300_000))
    signed(roster, season, imports, "Repriced", (80, 4, 7_000_000), (80, 4, 7_200_000))
    hurt = signed(roster, season, imports, "Hurt", (90, 4, 8_000_000), (90, 4, 8_000_000))
    signed(roster, season, imports, "Resting", (60, 4, 6_000_000), (60, 4, 6_000_000))
    news(hurt, 3, now, kind=PlayerNews.Kind.INJURY)

    report = report_for(roster, now)

    assert names(report.played) == ["Scorer"]
    assert names(report.salary_only) == ["Repriced"]
    assert names(report.news_only) == ["Hurt"]
    assert names(report.quiet) == ["Resting"]
    assert report.as_of == imports[1]
    assert report.previous_as_of == imports[0]
    assert report.squad_size == 4


def test_the_played_group_sorts_by_points_gained(roster, season, imports, now):
    signed(roster, season, imports, "Low", (100, 4), (110, 5))
    signed(roster, season, imports, "High", (100, 4), (160, 5))
    signed(roster, season, imports, "Negative", (100, 4), (97, 5))
    signed(roster, season, imports, "Zero", (100, 4), (100, 5))

    report = report_for(roster, now)

    assert names(report.played) == ["High", "Low", "Zero", "Negative"]


def test_a_row_carries_the_moves(roster, season, imports, now):
    signed(roster, season, imports, "Twice", (100, 4, 9_000_000), (160, 6, 8_800_000))

    (row,) = report_for(roster, now).played

    assert row.games == 2
    assert row.fp == Decimal("60")
    assert row.fp_per_game_change == Decimal("1.67")
    assert row.salary_change == Decimal("-200000")


def test_the_summary_adds_up(roster, season, imports, now):
    a = signed(roster, season, imports, "A", (100, 4, 9_000_000), (150, 5, 9_300_000))
    signed(roster, season, imports, "B", (100, 4, 7_000_000), (130.5, 5, 6_900_000))
    signed(roster, season, imports, "C", (80, 4, 5_000_000), (80, 4, 5_200_000))
    news(a, 2, now, kind=PlayerNews.Kind.INJURY)
    news(a, 5, now)

    report = report_for(roster, now)

    assert report.fp_total == Decimal("80.5")
    assert report.value_change == Decimal("400000")
    assert len(report.updated) == 3
    assert report.news_count == 2
    assert report.injury_count == 1


def test_a_player_who_played_keeps_his_news(roster, season, imports, now):
    scorer = signed(roster, season, imports, "Scorer", (100, 4), (150, 5))
    news(scorer, 2, now, headline="Older")
    injury = news(scorer, 1, now, kind=PlayerNews.Kind.INJURY, headline="Questionable")

    report = report_for(roster, now)

    (row,) = report.played
    assert report.news_only == []
    assert [item.headline for item in row.news] == ["Questionable", "Older"]
    assert row.lead_news == injury


def test_news_outside_the_window_or_the_roster_is_left_out(roster, season, imports, now):
    mine = signed(roster, season, imports, "Mine", (60, 4), (60, 4))
    stranger = make_player("Stranger")
    news(mine, 25, now)
    news(stranger, 1, now)
    news(None, 1, now)

    report = report_for(roster, now)

    assert report.news_count == 0
    assert names(report.quiet) == ["Mine"]


def test_injuries_lead_the_news_only_group(roster, season, imports, now):
    plain = signed(roster, season, imports, "Aaron", (60, 4), (60, 4))
    hurt = signed(roster, season, imports, "Zed", (60, 4), (60, 4))
    news(plain, 2, now)
    news(hurt, 2, now, kind=PlayerNews.Kind.INJURY)

    assert names(report_for(roster, now).news_only) == ["Zed", "Aaron"]


def test_sold_players_are_not_on_the_tab(roster, season, imports, now):
    gone = signed(roster, season, imports, "Gone", (100, 4), (150, 5))
    services.sell(roster, gone, gone.current_salary)

    report = report_for(roster, now)

    assert report.played == []
    assert report.squad_size == 0


def test_a_player_missing_from_the_newest_import_has_nothing_new(roster, season, imports, now):
    signed(roster, season, imports, "Listed", (100, 4), (150, 5))
    dropped = make_player("Dropped")
    snapshot(dropped, season, imports[0] - dt.timedelta(days=1), 50, 3)
    snapshot(dropped, season, imports[0], 90, 4)
    dropped.refresh_from_db()
    services.buy(roster, dropped, dropped.current_salary)

    report = report_for(roster, now)

    assert names(report.played) == ["Listed"]
    assert names(report.quiet) == ["Dropped"]


def test_a_season_with_one_import_has_nothing_to_compare(roster, season, now):
    player = make_player("Fresh")
    snapshot(player, season, now - dt.timedelta(hours=1), 30, 1)
    player.refresh_from_db()
    services.buy(roster, player, player.current_salary)

    report = report_for(roster, now)

    assert not report.has_comparison
    assert report.previous_as_of is None
    assert names(report.quiet) == ["Fresh"]


def test_quiet_players_carry_their_next_game(roster, season, imports, now):
    signed(roster, season, imports, "Resting", (60, 4), (60, 4))
    upcoming = Game.objects.create(
        espn_id="401",
        season_year=season.ends_on.year,
        tip_off=now + dt.timedelta(days=1),
        home_team=Team.objects.get(abbreviation="LAL"),
        away_team=Team.objects.get(abbreviation="BOS"),
        status="STATUS_SCHEDULED",
    )

    (row,) = report_for(roster, now).quiet

    assert row.schedule.next_game == upcoming


def test_the_report_costs_a_fixed_number_of_queries(
    roster, season, imports, now, django_assert_max_num_queries
):
    for index in range(6):
        player = signed(roster, season, imports, f"P{index}", (100, 4), (120 + index, 5))
        news(player, 2, now)
    fresh = Roster.objects.get(pk=roster.pk)

    # Squad + its two prefetches, the import stamps, the news, the schedule.
    with django_assert_max_num_queries(7):
        report = roster_latest(fresh, now=now)
        assert [row.lead_news for row in report.played]


# --- the page ----------------------------------------------------------------


@pytest.fixture
def signed_in(client, user, password):
    client.login(email=user.email, password=password)
    return client


def latest_url(roster):
    return reverse("fantasy:roster-latest", args=[roster.pk])


def test_the_page_shows_the_groups_and_the_news(signed_in, roster, season, imports, now):
    scorer = signed(roster, season, imports, "Scorer", (100, 4, 9_000_000), (158.5, 5, 9_300_000))
    signed(roster, season, imports, "Resting", (60, 4), (60, 4))
    news(scorer, 2, now, kind=PlayerNews.Kind.INJURY, headline="Scorer (ankle) questionable")

    response = signed_in.get(latest_url(roster))

    assert response.status_code == 200
    body = response.text
    assert "Played" in body
    assert "+58.5" in body
    assert "Scorer (ankle) questionable" in body
    assert "No update" in body
    assert [group["key"] for group in response.context["groups"]] == ["played"]


def test_the_page_says_when_there_is_nothing_to_compare(signed_in, roster, season, now):
    player = make_player("Fresh")
    snapshot(player, season, now - dt.timedelta(hours=1), 30, 1)
    player.refresh_from_db()
    services.buy(roster, player, player.current_salary)

    body = signed_in.get(latest_url(roster)).text

    assert "first import" in body


def test_an_empty_roster_points_back_to_building_it(signed_in, roster):
    body = signed_in.get(latest_url(roster)).text

    assert "No players on this roster yet." in body


def test_someone_elses_roster_is_not_found(signed_in, other_manager, season):
    theirs = Roster.objects.create(manager=other_manager, season=season, name="Theirs")

    assert signed_in.get(latest_url(theirs)).status_code == 404


def test_signed_out_visitors_are_sent_to_sign_in(client, roster):
    response = client.get(latest_url(roster))

    assert response.status_code == 302
    assert "login" in response["Location"]
