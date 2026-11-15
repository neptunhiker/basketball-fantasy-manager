"""What the AI is shown: `apps.reports.inputs`."""

import datetime as dt
from decimal import Decimal
from itertools import count

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.nba.models import Game, PlayerInjury, PlayerNews
from apps.reports import inputs
from tests.reports_factories import (
    at,
    make_player,
    make_roster,
    make_season,
    make_team,
    snapshot,
)

pytestmark = pytest.mark.django_db

NOW = at(11, 15, 12, 30)
LATEST = at(11, 15, 12, 15)
WEEK_AGO = at(11, 8, 12, 15)
_news_ids = count(1)


@pytest.fixture
def season():
    return make_season()


@pytest.fixture
def teams():
    home, away = make_team("SAS"), make_team("LAL")
    # Two games in the next week and one later, all between the two teams.
    for serial, day in enumerate((16, 18, 30)):
        Game.objects.create(
            espn_id=f"g{serial}",
            season_year=2027,
            tip_off=at(11, day, 20),
            home_team=home,
            away_team=away,
        )
    return home, away


def played(season, team, fp_per_game="20", games=8, position="G", salary="3", before=None):
    """A player with this week's snapshot and, unless `before` is False, last week's."""
    who = make_player(position, salary, fp_per_game, games, team=team)
    if before is not False:
        total, games_then = before or (Decimal(fp_per_game) * (games - 3), games - 3)
        snapshot(season, who, WEEK_AGO, total=total, games=games_then)
    snapshot(season, who, LATEST)
    return who


def hurt(player, status="Out", returns=dt.date(2026, 11, 30), **extra):
    return PlayerInjury.objects.create(
        player=player,
        observed_at=at(11, 14),
        status=status,
        return_date=returns,
        injury_type=extra.pop("injury_type", "Knee"),
        **extra,
    )


def news(player, day, headline="Headline", body="Body"):
    return PlayerNews.objects.create(
        rotowire_news_id=next(_news_ids),
        player=player,
        feed_player_name=player.full_name,
        headline=headline,
        body=body,
        url="https://example.com/news",
        published_on=dt.date(2026, 11, day),
    )


def line_of(text, name):
    return next(line for line in text.splitlines() if f"| {name} |" in line)


def test_nothing_to_report_without_snapshots(season):
    with pytest.raises(inputs.NoReportData):
        inputs.build(season, now=NOW)


def test_players_are_named_with_refs_best_scorer_first(season, teams):
    star = played(season, teams[0], fp_per_game="40")
    regular = played(season, teams[1], fp_per_game="20")
    make_player(fp_per_game="0", games=0)  # never played, on no roster: left out

    built = inputs.build(season, now=NOW)

    assert built.data_as_of == LATEST
    assert built.player_refs == {"P001": str(star.pk), "P002": str(regular.pk)}
    assert f"P001 | {star.full_name} | SAS | G | 3.00 | 40.0" in built.shared_input
    assert "P003" not in built.shared_input


def test_form_is_shown_per_week_from_the_snapshots(season, teams):
    # 100 points in 5 games a week ago; 160 in 8 games now: 60 in 3 games.
    who = played(season, teams[0], fp_per_game="20", games=8, before=(Decimal(100), 5))
    newcomer = played(season, teams[0], before=False)

    shared = inputs.build(season, now=NOW).shared_input

    assert "FP/game by week, oldest to newest, the last week ending 2026-11-15" in shared
    assert " | 20.0 | - (0) → - (0) → 20.0 (3) | " in line_of(shared, who.full_name)
    assert " | 20.0 | - (0) → - (0) → - (0) | " in line_of(shared, newcomer.full_name)


def test_projection_and_schedule_come_from_the_games(season, teams):
    who = played(season, teams[0], fp_per_game="20")
    shared = inputs.build(season, now=NOW).shared_input
    # Two games next week; 82 less none played is 82 left; 20 x 82 x 0.85.
    assert " | 2 | 82 | 1394 | " in line_of(shared, who.full_name)
    assert "SAS 2/82" in shared and "LAL 2/82" in shared


def test_injuries_are_listed_and_counted_per_team(season, teams):
    out = played(season, teams[0])
    doubtful = played(season, teams[0])
    fine = played(season, teams[1])
    hurt(out, injury_location="Leg")
    hurt(doubtful, status="Day-To-Day", returns=None)
    hurt(fine, status="Available")

    shared = inputs.build(season, now=NOW).shared_input

    assert line_of(shared, out.full_name).endswith("| OUT to 2026-11-30")
    assert line_of(shared, doubtful.full_name).endswith("| DTD")
    assert line_of(shared, fine.full_name).endswith("| -")
    injuries = shared.split("INJURIES", 1)[1].split("SCHEDULE", 1)[0]
    assert f"{out.full_name} | SAS | Out | Knee, Leg | 2026-11-30 | 2026-11-14" in injuries
    assert fine.full_name not in injuries
    assert "Injured players per team: SAS 2" in injuries


def test_news_puts_rostered_players_first_and_stays_within_its_budget(
    season, teams, manager, monkeypatch
):
    mine = played(season, teams[0], fp_per_game="5")
    star = played(season, teams[1], fp_per_game="40")
    news(star, 15, headline="Star news", body="y" * 1000)
    news(mine, 10, headline="My news", body="x" * 1000)
    news(star, 8, headline="Too old")
    roster = make_roster(season, manager, [mine])

    shared = inputs.build(season, [roster], now=NOW).shared_input
    block = shared.split("NEWS", 1)[1]
    assert block.index("My news") < block.index("Star news")
    assert "Too old" not in block
    assert "x" * 380 in block and "x" * 400 not in block  # cut at 400 characters

    # Room for the header and one item of about 450 characters, not two.
    monkeypatch.setattr(inputs, "NEWS_CHARS", 600)
    block = inputs.build(season, [roster], now=NOW).shared_input.split("NEWS", 1)[1]
    assert "My news" in block and "Star news" not in block


def test_a_roster_block_has_its_squad_and_legal_trades_but_no_identities(season, teams, manager):
    squad = [played(season, teams[0], fp_per_game="10") for _ in range(2)]
    upgrade = played(season, teams[1], fp_per_game="30", salary="4")
    roster = make_roster(season, manager, squad, cash="1", trades=0, name="Koala Kings")

    built = inputs.build(season, [roster], now=NOW)
    entry = built.rosters[0]

    assert entry.roster == roster
    assert f"{squad[0].full_name} (G)" in entry.own_input
    assert "Cash: $1.00M | Trades available: 0" in entry.own_input
    assert "still affordable after buying a trade" in entry.own_input
    assert [trade["in"] for trade in entry.shortlist] == [str(upgrade.pk), str(upgrade.pk)]
    assert entry.shortlist[0]["ref"] == "T01"
    everything = built.shared_input + entry.own_input
    assert "Koala Kings" not in everything
    assert manager.nick_name not in everything
    assert manager.user.email not in everything


def test_rules_name_the_next_salary_update_and_free_trade(season, teams):
    season.trade_market_opens_at = at(11, 1, 22)
    season.weekly_trades_per_week = 1
    season.save()
    played(season, teams[0])
    rules = inputs.build(season, now=NOW).shared_input.split("PLAYERS", 1)[0]
    assert "Data as of Sun 2026-11-15 12:15" in rules
    assert "Next salary update: Sun 2026-11-15 22:00." in rules


def test_queries_do_not_grow_with_rosters(season, teams, manager):
    players = [played(season, teams[n % 2], fp_per_game=str(10 + n)) for n in range(6)]
    one = [make_roster(season, manager, players[:3])]
    three = one + [make_roster(season, manager, players[n : n + 3]) for n in (1, 2)]

    with CaptureQueriesContext(connection) as single:
        inputs.build(season, one, now=NOW)
    with CaptureQueriesContext(connection) as triple:
        inputs.build(season, three, now=NOW)
    assert len(triple) == len(single)
    assert len(single) <= 20
