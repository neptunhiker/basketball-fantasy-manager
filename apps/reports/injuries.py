"""Injuries as stories, not snapshots: how long, how it changed, who it helps.

The injury import writes one row per injured player each time it runs, and a
"Healthy" row for each player who drops off the feed. Read in order, a
player's rows give his *spell*: the unbroken run of hurt reports ending at the
newest one. From that:

- **out since**: the first report of the spell (the first time we saw it),
- **team games missed** since then, from the season's games,
- **changes**: when the status or the expected return moved, repeats left out,
- **returned**: a spell that ended recently with a healthy report,
- **who benefits**: teammates whose points rose since a long absence began.

Who-benefits shows timing, not cause: the block says "since", never "because".

Reads only: four queries (reports, newest report, news, games), whatever the
player count.
"""

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Max
from django.utils import timezone

from apps.nba.models import (
    DAY_TO_DAY_INJURY_STATUSES,
    HEALTHY_INJURY_STATUSES,
    PlayerInjury,
    PlayerNews,
)
from apps.nba.team_schedule import season_games

HISTORY_DAYS = 21
HISTORY_CHANGES = 3
RETURNED_DAYS = 14
HEADLINE_DAYS = 7
COMMENT_CHARS = 180
# Who benefits: an absence long enough to change roles, of a player who
# scored enough for his minutes to matter.
BENEFIT_MIN_GAMES_MISSED = 3
BENEFIT_RETURN_DAYS = 7
BENEFIT_MIN_FP = Decimal(15)
BENEFIT_TEAMMATES = 3
BENEFIT_MIN_GAMES = 2
BENEFIT_BEFORE_DAYS = 21


def is_hurt(status):
    return bool(status) and status.lower() not in HEALTHY_INJURY_STATUSES


def is_day_to_day(status):
    return bool(status) and status.lower() in DAY_TO_DAY_INJURY_STATUSES


@dataclass(frozen=True)
class Change:
    """A report that said something new: a status or an expected return."""

    on: dt.date
    status: str
    return_date: dt.date | None
    status_changed: bool
    return_changed: bool


@dataclass
class Spell:
    """One player's current or just-ended absence."""

    player: object
    latest: PlayerInjury
    out_since: dt.datetime
    games_missed: int = 0
    # The state at the start of the history window, then what changed.
    history: list = field(default_factory=list)
    returned_at: dt.datetime | None = None
    headline: tuple | None = None

    @property
    def is_returned(self):
        return self.returned_at is not None

    @property
    def returned_on(self):
        return None if self.returned_at is None else timezone.localdate(self.returned_at)

    @property
    def status(self):
        return self.latest.status

    @property
    def return_date(self):
        return None if self.is_returned else self.latest.return_date

    @property
    def injury(self):
        latest = self.latest
        parts = (latest.injury_type, latest.injury_location, latest.injury_detail)
        return ", ".join(part for part in parts if part)

    @property
    def comment(self):
        """The provider's latest note, for players out or who matter: a
        day-to-day bench player's note is rarely more than his status."""
        if is_day_to_day(self.status) and (self.player.current_fp_per_game or 0) < BENEFIT_MIN_FP:
            return ""
        text = " ".join((self.latest.short_comment or "").split())
        if len(text) > COMMENT_CHARS:
            text = text[: COMMENT_CHARS - 1].rstrip() + "…"
        return text

    def is_long(self, today):
        """Out long enough for teammates' roles to change."""
        if self.is_returned or is_day_to_day(self.status):
            return self.games_missed >= BENEFIT_MIN_GAMES_MISSED
        if self.games_missed >= BENEFIT_MIN_GAMES_MISSED:
            return True
        back = self.return_date
        return back is None or back > today + dt.timedelta(days=BENEFIT_RETURN_DAYS)


@dataclass(frozen=True)
class Gain:
    """A teammate's form before and since an absence began."""

    player: object
    before: object
    since: object

    @property
    def rise(self):
        return self.since.per_game - self.before.per_game


@dataclass
class Benefit:
    """Who has gained since a long absence began."""

    spell: Spell
    teammates: list
    also_out: list


@dataclass
class InjuryReport:
    as_of: dt.datetime | None
    hurt: list
    returned: list
    benefits: list

    @property
    def openings(self):
        """{player id: injured player} for every teammate who rose since an absence."""
        return {
            gain.player.pk: benefit.spell.player
            for benefit in self.benefits
            for gain in benefit.teammates
        }

    def spell_of(self, player_id):
        for spell in (*self.hurt, *self.returned):
            if spell.player.pk == player_id:
                return spell
        return None


def build(season, players, log, data_as_of, now):
    """The injury report for `players` (the run's player table).

    Injuries are read up to `now`, the moment the run starts: the daily
    injury import comes after the snapshot that sets `data_as_of`. Form is
    read up to `data_as_of`, as everywhere else in the run.
    """
    by_id = {player.pk: player for player in players}
    rows = defaultdict(list)
    for report in (
        PlayerInjury.objects.filter(player_id__in=by_id, observed_at__lte=now)
        .order_by("player_id", "observed_at", "created_at")
        .only(
            "player_id",
            "observed_at",
            "status",
            "return_date",
            "injury_type",
            "injury_location",
            "injury_detail",
            "short_comment",
        )
    ):
        rows[report.player_id].append(report)
    as_of = PlayerInjury.objects.filter(observed_at__lte=now).aggregate(moment=Max("observed_at"))[
        "moment"
    ]

    today = timezone.localdate(now)
    spells = []
    for player_id, reports in rows.items():
        spell = _spell(by_id[player_id], reports, today)
        if spell is not None:
            spells.append(spell)

    _count_games_missed(season, spells, log, now)
    _attach_headlines(spells, today)

    hurt = [spell for spell in spells if not spell.is_returned]
    returned = [spell for spell in spells if spell.is_returned]
    benefits = _benefits(spells, players, log, data_as_of, today)
    order = {player.pk: index for index, player in enumerate(players)}
    hurt.sort(key=lambda spell: order[spell.player.pk])
    returned.sort(key=lambda spell: order[spell.player.pk])
    return InjuryReport(as_of=as_of, hurt=hurt, returned=returned, benefits=benefits)


def _spell(player, reports, today):
    newest = reports[-1]
    if is_hurt(newest.status):
        end = len(reports)
        returned_at = None
        latest = newest
    else:
        # Healthy now: a spell only if it ended within RETURNED_DAYS.
        if len(reports) < 2 or not is_hurt(reports[-2].status):
            return None
        returned_at = newest.observed_at
        if timezone.localdate(returned_at) < today - dt.timedelta(days=RETURNED_DAYS):
            return None
        end = len(reports) - 1
        latest = reports[-2]
    start = end - 1
    while start > 0 and is_hurt(reports[start - 1].status):
        start -= 1
    spell_reports = reports[start:end]
    return Spell(
        player=player,
        latest=latest,
        out_since=spell_reports[0].observed_at,
        history=_history(spell_reports, today),
        returned_at=returned_at,
    )


def _history(reports, today):
    """The state when the window opens (or the spell began), then each change."""
    since = today - dt.timedelta(days=HISTORY_DAYS)
    changes = []
    previous = None
    for report in reports:
        status_changed = previous is not None and report.status != previous.status
        return_changed = previous is not None and report.return_date != previous.return_date
        if previous is None or status_changed or return_changed:
            changes.append(
                Change(
                    on=timezone.localdate(report.observed_at),
                    status=report.status,
                    return_date=report.return_date,
                    status_changed=status_changed,
                    return_changed=return_changed,
                )
            )
        previous = report
    first, rest = changes[0], changes[1:]
    rest = [change for change in rest if change.on >= since][-HISTORY_CHANGES:]
    return [first, *rest]


def _count_games_missed(season, spells, log, now):
    """His team's games since the spell began that he did not play in."""
    if not spells:
        return
    earliest = min(spell.out_since for spell in spells)
    games = list(
        season_games(season)
        .filter(tip_off__gt=earliest, tip_off__lte=now)
        .values_list("tip_off", "home_team_id", "away_team_id")
    )
    for spell in spells:
        team_id = spell.player.team_id
        if team_id is None:
            continue
        end = spell.returned_at or now
        team_games = sum(
            1
            for tip_off, home, away in games
            if spell.out_since < tip_off <= end and team_id in (home, away)
        )
        played = log.window(spell.player.pk, spell.out_since, end).games
        spell.games_missed = max(team_games - played, 0)


def _attach_headlines(spells, today):
    """Each player's newest RotoWire injury headline from the last week."""
    if not spells:
        return
    newest = {}
    for player_id, published_on, headline in (
        PlayerNews.objects.filter(
            player_id__in=[spell.player.pk for spell in spells],
            kind=PlayerNews.Kind.INJURY,
            published_on__gte=today - dt.timedelta(days=HEADLINE_DAYS - 1),
            published_on__lte=today,
        )
        .order_by("player_id", "-published_on", "-rotowire_news_id")
        .values_list("player_id", "published_on", "headline")
    ):
        newest.setdefault(player_id, (published_on, headline))
    for spell in spells:
        spell.headline = newest.get(spell.player.pk)


def _benefits(spells, players, log, data_as_of, today):
    hurt_now = {spell.player.pk for spell in spells if not spell.is_returned}
    by_team = defaultdict(list)
    for player in players:
        if player.team_id is not None:
            by_team[player.team_id].append(player)
    benefits = []
    for spell in spells:
        injured = spell.player
        if spell.is_returned or injured.team_id is None:
            continue
        if (injured.current_fp_per_game or 0) < BENEFIT_MIN_FP or not spell.is_long(today):
            continue
        gains = []
        for mate in by_team[injured.team_id]:
            if mate.pk == injured.pk or mate.pk in hurt_now:
                continue
            gain = _gain(log, mate, injured.team_id, spell.out_since, data_as_of)
            if gain is not None and gain.rise > 0:
                gains.append(gain)
        gains.sort(
            key=lambda gain: (gain.player.position != injured.position, -gain.rise, gain.player.pk)
        )
        also_out = [
            other.player
            for other in spells
            if not other.is_returned
            and other.player.pk != injured.pk
            and other.player.team_id == injured.team_id
            and not is_day_to_day(other.status)
        ]
        benefits.append(
            Benefit(spell=spell, teammates=gains[:BENEFIT_TEAMMATES], also_out=also_out)
        )
    order = {player.pk: index for index, player in enumerate(players)}
    benefits.sort(key=lambda benefit: order[benefit.spell.player.pk])
    return benefits


def _gain(log, mate, team_id, out_since, data_as_of):
    since = log.window(mate.pk, out_since, data_as_of, team_id=team_id)
    if since.games < BENEFIT_MIN_GAMES:
        return None
    before = log.window(
        mate.pk, out_since - dt.timedelta(days=BENEFIT_BEFORE_DAYS), out_since, team_id=team_id
    )
    if before.games < BENEFIT_MIN_GAMES:
        before = log.window(mate.pk, None, out_since, team_id=team_id)
    if before.games < BENEFIT_MIN_GAMES:
        return None
    return Gain(player=mate, before=before, since=since)
