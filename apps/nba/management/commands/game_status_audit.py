import datetime as dt
import itertools
import statistics

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.fantasy.models import PlayerSnapshot, Season
from apps.nba.game_status import (
    MISSED,
    PARTLY,
    PLAYED,
    schedule_for_seasons,
    status_between,
)
from apps.nba.models import GAME_LENGTH, SOURCE_DELAY

# Longer than this after a game, a late count is a correction, not a slow source.
LAG_LIMIT = dt.timedelta(hours=24)


class Command(BaseCommand):
    help = (
        "Check the game status against the snapshots: how often basketball.de counted "
        "a game later than the first read after it ended. Read-only; changes nothing."
    )

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=14, help="How far back to look.")

    def handle(self, *args, days=14, **options):
        season = Season.objects.filter(is_current=True).first()
        if season is None:
            self.stdout.write("No current season.")
            return
        since = timezone.now() - dt.timedelta(days=days)
        snapshots = list(
            PlayerSnapshot.objects.filter(season=season, as_of__gte=since)
            .order_by("player_id", "as_of")
            .only("player_id", "season_id", "as_of", "team_id", "games_played")
        )
        schedule = schedule_for_seasons({season.pk})
        if not schedule:
            self.stdout.write("No schedule loaded for this season.")
            return

        reads = {}
        uncounted_for = []
        unscheduled = 0
        for older, newer in itertools.pairwise(snapshots):
            if older.player_id != newer.player_id:
                continue
            status = status_between(newer, older, schedule)
            counts = reads.setdefault(
                (older.as_of, newer.as_of),
                {"games": 0, MISSED: 0, PARTLY: 0, "late": 0, "unscheduled": 0},
            )
            if status.games:
                counts["games"] += 1
            if status.kind in (MISSED, PARTLY):
                counts[status.kind] += 1
            in_window = schedule.between(status.team_ids, older.as_of, newer.as_of)
            extra = status.played - len(in_window) if status.kind == PLAYED else 0
            if extra <= 0:
                continue
            # More games counted than his teams played in the window: either a
            # game from before it that the source took in late, or one the
            # stored schedule does not have at all.
            late = [game for game in status.games if game.tip_off < older.as_of]
            for game in late:
                uncounted_for.append(older.as_of - (game.tip_off + GAME_LENGTH))
            counts["late"] += bool(late)
            counts["unscheduled"] += extra > len(late)
            unscheduled += extra > len(late)

        self.stdout.write(
            f"Season {season.label}, last {days} days, {len(reads)} pairs of reads, "
            f"SOURCE_DELAY = {SOURCE_DELAY}."
        )
        self.stdout.write(
            "Read → next read: players whose team played / DNP / partly / "
            "counted late / not in schedule"
        )
        for (start, end), counts in sorted(reads.items()):
            if not any(counts.values()):
                continue
            self.stdout.write(
                f"  {_stamp(start)} → {_stamp(end)}: {counts['games']:>3} / "
                f"{counts[MISSED]:>3} / {counts[PARTLY]:>3} / {counts['late']:>3} / "
                f"{counts['unscheduled']:>3}"
            )

        if unscheduled:
            self.stdout.write(
                self.style.WARNING(
                    f"{unscheduled} times a player had more games than the stored schedule "
                    "gives his team. Reload the schedule (import_nba_schedule); until then "
                    "those games show as played without an opponent."
                )
            )
        # A source that is slow counts a game within hours. Days later is a
        # correction after the fact (a player added to the list with his
        # earlier games), which no delay setting can foresee.
        slow = [lag for lag in uncounted_for if lag <= LAG_LIMIT]
        corrections = len(uncounted_for) - len(slow)
        if corrections:
            self.stdout.write(
                f"{corrections} games were added more than {_hours(LAG_LIMIT)} after they "
                "ended: corrections after the fact. They show as DNP until the correction "
                "arrives, then as played; SOURCE_DELAY cannot help with those."
            )
        if not slow:
            self.stdout.write(
                self.style.SUCCESS("No game was counted late. SOURCE_DELAY can stay.")
            )
            return
        longest = max(slow)
        self.stdout.write(
            self.style.WARNING(
                f"{len(slow)} games were counted late: still missing up to {_hours(longest)} "
                f"after they ended (median {_hours(statistics.median(slow))}). Set "
                f"SOURCE_DELAY to at least {_hours(longest)} to read them as played, not DNP."
            )
        )


def _stamp(moment):
    return timezone.localtime(moment).strftime("%a %d.%m. %H:%M")


def _hours(delta):
    minutes = int(delta.total_seconds() // 60)
    return f"{minutes // 60}h{minutes % 60:02d}"
