"""What the newest import and the last day's news changed for one roster.

Read by the roster's Latest tab. The stats side compares each player's two
newest snapshots, which are the newest two basketball.de imports: every run
writes one snapshot per player, played or not. The news side is a plain
24-hour window on when RotoWire items were first seen here -- the feed dates
its items but does not time them.

Nothing here queries per player. The squad comes with its snapshots
prefetched (`RosterPlayer.objects.as_squad`), the news is one query and the
roster's season schedule another.
"""

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from django.utils import timezone

from apps.nba.models import PlayerNews
from apps.nba.team_schedule import season_games, team_schedules

from .models import PlayerSnapshot

NEWS_WINDOW = dt.timedelta(hours=24)


@dataclass
class LatestRow:
    """One player on the tab: what moved, what was written about him."""

    player: object
    # `latest_fp_change`'s dict, or None when the newest import is not his
    # newest snapshot -- a player the import no longer lists has nothing new.
    change: dict | None
    news: list = field(default_factory=list)
    schedule: object = None

    @property
    def games(self):
        return self.change["games"] if self.change else 0

    @property
    def played(self):
        return self.games > 0

    @property
    def fp(self):
        return self.change["fp"] if self.change else None

    @property
    def fp_per_game_change(self):
        return self.change["fp_per_game"] if self.change else None

    @property
    def salary_change(self):
        return self.change["salary"] if self.change else None

    @property
    def salary_moved(self):
        return bool(self.salary_change)

    @property
    def injury_news(self):
        return [item for item in self.news if item.is_injury]

    @property
    def lead_news(self):
        """The item the one-line cell shows: an injury beats ordinary news."""
        return (self.injury_news or self.news or [None])[0]


@dataclass
class LatestReport:
    as_of: dt.datetime | None
    previous_as_of: dt.datetime | None
    news_since: dt.datetime
    squad_size: int
    played: list = field(default_factory=list)
    salary_only: list = field(default_factory=list)
    news_only: list = field(default_factory=list)
    quiet: list = field(default_factory=list)

    @property
    def updated(self):
        return self.played + self.salary_only

    @property
    def games_total(self):
        return sum(row.games for row in self.played)

    @property
    def fp_total(self):
        return sum((row.fp for row in self.played if row.fp is not None), Decimal("0"))

    @property
    def value_change(self):
        return sum((row.salary_change for row in self.updated if row.salary_change), Decimal("0"))

    @property
    def news_rows(self):
        return [row for row in self.played + self.salary_only + self.news_only if row.news]

    @property
    def news_count(self):
        return sum(len(row.news) for row in self.news_rows)

    @property
    def injury_count(self):
        return sum(len(row.injury_news) for row in self.news_rows)

    @property
    def has_comparison(self):
        return self.previous_as_of is not None


def import_stamps(season):
    """The newest two import moments of `season`, newest first; None for gaps."""
    stamps = list(
        PlayerSnapshot.objects.filter(season=season)
        .order_by("-as_of")
        .values_list("as_of", flat=True)
        .distinct()[:2]
    )
    stamps += [None] * (2 - len(stamps))
    return stamps


def news_by_player(player_ids, since):
    news = {}
    items = PlayerNews.objects.filter(player_id__in=player_ids, first_seen_at__gte=since).order_by(
        "-published_on", "-rotowire_news_id"
    )
    for item in items:
        news.setdefault(item.player_id, []).append(item)
    return news


def roster_latest(roster, now=None):
    """The Latest tab for `roster`: its players sorted into four groups.

    * played -- at least one game since the previous import, most points first
    * salary_only -- no game, but the game repriced him; biggest move first
    * news_only -- nothing in the stats, something in the news; injuries first
    * quiet -- nothing at all, in the squad's usual order, with the next game

    A player who played keeps his news on his row, so the news groups only
    hold players the stats say nothing about.
    """
    now = now or timezone.now()
    as_of, previous_as_of = import_stamps(roster.season)
    since = now - NEWS_WINDOW
    players = roster.current_squad
    news = news_by_player([player.pk for player in players], since)
    # The roster's own season, which also spares looking up the current one.
    schedules = team_schedules(now=now, games=season_games(roster.season))

    report = LatestReport(
        as_of=as_of,
        previous_as_of=previous_as_of,
        news_since=since,
        squad_size=len(players),
    )
    for player in players:
        change = player.latest_fp_change()
        if change is not None and change["as_of"] != as_of:
            change = None
        row = LatestRow(
            player=player,
            change=change,
            news=news.get(player.pk, []),
            schedule=schedules.get(player.team_id),
        )
        if row.played:
            report.played.append(row)
        elif row.salary_moved:
            report.salary_only.append(row)
        elif row.news:
            report.news_only.append(row)
        else:
            report.quiet.append(row)

    # A negative night is still a game, so it sorts below the scoreless one.
    report.played.sort(key=lambda row: (-(row.fp or 0), -row.games, row.player.last_name))
    report.salary_only.sort(key=lambda row: (-abs(row.salary_change), row.player.last_name))
    report.news_only.sort(key=lambda row: (not row.injury_news, row.player.last_name))
    return report
