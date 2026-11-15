"""Building blocks for the AI trade report tests (`apps.reports`).

Plain functions rather than fixtures, so each test says what world it needs.
"""

import datetime as dt
from decimal import Decimal
from itertools import count
from zoneinfo import ZoneInfo

from apps.fantasy.models import PlayerSnapshot, Roster, RosterPlayer, Season
from apps.nba.models import Player, Team
from apps.reports import catalog
from apps.reports.models import Report, ReportRun

BERLIN = ZoneInfo("Europe/Berlin")
MILLION = Decimal(1_000_000)
_serial = count(1)


def at(month, day, hour=12, minute=0, year=2026):
    return dt.datetime(year, month, day, hour, minute, tzinfo=BERLIN)


def make_season(**extra):
    defaults = {
        "label": "2026-27",
        "starts_on": dt.date(2026, 10, 21),
        "ends_on": dt.date(2027, 4, 11),
        "is_current": True,
        "signings_close_at": at(10, 20, 23),
        "starting_cash": Decimal("63000000"),
    }
    defaults.update(extra)
    return Season.objects.create(**defaults)


def make_team(abbreviation=None):
    serial = next(_serial)
    abbreviation = abbreviation or f"T{serial:02d}"[:4]
    return Team.objects.get_or_create(
        abbreviation=abbreviation, defaults={"name": f"Team {abbreviation} {serial}"}
    )[0]


def make_player(position="G", salary="3", fp_per_game="20", games=10, team=None, **extra):
    """A player whose current figures are set as an import would set them."""
    serial = next(_serial)
    salary = Decimal(salary) * MILLION
    fp_per_game = Decimal(fp_per_game)
    return Player.objects.create(
        first_name=extra.pop("first_name", "Pat"),
        last_name=extra.pop("last_name", f"Player{serial:03d}"),
        position=position,
        team=team,
        current_salary=salary,
        current_fp_per_game=fp_per_game,
        current_total_fp=fp_per_game * games,
        current_games_played=games,
        **extra,
    )


def snapshot(season, player, moment, *, salary=None, total=None, games=None, position=None):
    """A snapshot of `player` at `moment`, defaulting to their current figures."""
    return PlayerSnapshot.objects.create(
        player=player,
        season=season,
        as_of=moment,
        salary=salary if salary is not None else player.current_salary,
        total_fp=Decimal(total) if total is not None else player.current_total_fp,
        games_played=games if games is not None else player.current_games_played,
        team=player.team,
        position=position or player.position,
    )


def make_roster(season, manager, players=(), cash="5", trades=1, name=None, **extra):
    roster = Roster.objects.create(
        manager=manager,
        season=season,
        name=name or f"Roster {next(_serial)}",
        **extra,
    )
    roster.cash = Decimal(cash) * MILLION
    roster.trades_available = trades
    roster.save(update_fields=["cash", "trades_available"])
    for player in players:
        RosterPlayer.objects.create(roster=roster, player=player, added_at=at(10, 10))
    return roster


def make_run(season, **extra):
    defaults = {
        "trigger": ReportRun.Trigger.MANUAL,
        "model": catalog.DEFAULT_MODEL,
        "effort": catalog.MEDIUM,
        "prompt_version": "test",
        "data_as_of": at(11, 15, 12, 15),
        "shared_input": "shared",
    }
    defaults.update(extra)
    return ReportRun.objects.create(season=season, **defaults)


def make_report(run, roster=None, **extra):
    kind = Report.Kind.ROSTER if roster is not None else Report.Kind.BRIEFING
    return Report.objects.create(run=run, roster=roster, kind=kind, **extra)
