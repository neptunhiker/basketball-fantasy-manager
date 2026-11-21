"""A season with a past to correct, for the move-correction tests.

Built around the real date rather than a fixed one, because the services stamp
and check against `timezone.now()`: free trades are paid out up to now, and a
correction may not land in the future. The season started about five weeks ago
and runs on for months, with its first salary update on a Sunday at 22:00 four
weeks back -- so every test has two whole salary weeks behind it to move moves
around in, and today still inside the season.
"""

import datetime as dt
from decimal import Decimal

from django.utils import timezone

from apps.fantasy import services
from apps.fantasy.models import PlayerSnapshot, Roster, Season, TradeGrant
from apps.nba.models import Player

M = Decimal("1000000")


def at(day, hour=14, minute=0):
    """A Berlin wall-clock moment on `day`."""
    return timezone.make_aware(dt.datetime.combine(day, dt.time(hour, minute)))


def first_update_sunday():
    """A Sunday at least four weeks back: when salaries first updated."""
    day = timezone.localdate() - dt.timedelta(days=28)
    while day.weekday() != 6:
        day -= dt.timedelta(days=1)
    return day


class World:
    """One season, one roster with a starting squad, and helpers to make history."""

    def __init__(self, manager, start_trades=5, starting_cash=Decimal("60000000")):
        self.sunday = first_update_sunday()
        self.season = Season.objects.create(
            label="2026-27",
            starts_on=self.sunday - dt.timedelta(days=10),
            ends_on=timezone.localdate() + dt.timedelta(days=120),
            is_current=True,
            starting_cash=starting_cash,
            trade_market_opens_at=at(self.sunday, 22),
        )
        self.roster = Roster.objects.create(manager=manager, season=self.season, name="Koalas")
        if start_trades:
            TradeGrant.objects.create(
                season=self.season,
                granted_at=at(self.season.starts_on, 12),
                trades=start_trades,
                label="Start trades",
            )
        self._count = 0

    # The week right after the first update, and the one after that.
    def week1(self, offset):
        return self.sunday + dt.timedelta(days=offset)

    def week2(self, offset):
        return self.sunday + dt.timedelta(days=7 + offset)

    def player(self, salary_millions, position="G", name=None):
        self._count += 1
        return Player.objects.create(
            first_name="P",
            last_name=name or f"Player{self._count}",
            position=position,
            current_salary=Decimal(salary_millions) * M,
        )

    def sign(self, player):
        """Bought before the season, as every squad is."""
        return services.buy(
            self.roster,
            player,
            player.current_salary,
            occurred_at=at(self.season.starts_on - dt.timedelta(days=5)),
        )

    def squad(self, positions="GGGGGFFFFFCCCCC", salary=2):
        players = [self.player(salary, position) for position in positions]
        for player in players:
            self.sign(player)
        return players

    def trade(self, out, in_, day, hour=14, price_out=None, price_in=None):
        return services.trade(
            self.roster,
            out,
            in_,
            price_out=out.current_salary if price_out is None else price_out,
            price_in=in_.current_salary if price_in is None else price_in,
            occurred_at=at(day, hour),
        )

    def buy_trade(self, day, hour=14):
        return services.buy_trade(self.roster, occurred_at=at(day, hour))

    def sell_trade(self, day, hour=14):
        return services.sell_trade(self.roster, occurred_at=at(day, hour))

    def snapshot(self, player, as_of, salary_millions):
        return PlayerSnapshot.objects.create(
            player=player,
            season=self.season,
            as_of=as_of,
            salary=Decimal(salary_millions) * M,
            total_fp=Decimal("0"),
            games_played=0,
            position=player.position,
        )

    def reload(self):
        self.roster.refresh_from_db()
        return self.roster
