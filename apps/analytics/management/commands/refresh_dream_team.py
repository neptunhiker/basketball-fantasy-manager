"""Work out the Dream Team again, for the newest import or for all of them.

Every basketball.de import does this by itself. The command is for the times
it has to be redone: after a season's draft date or budget was changed in the
admin, or to fill in the history of imports from before the Dream Team existed
(`--all`), so the page has a previous result to compare with straight away.
"""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.analytics.dream_team import NoDreamTeam, changes, refresh
from apps.fantasy.models import PlayerSnapshot, Season


class Command(BaseCommand):
    help = (
        "Recalculate the Dream Team of the newest import, or with --all of every import "
        "of the season, oldest first. Overwrites results already stored for those imports."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--season", help="Season label, e.g. 2026-27. Default: the current one."
        )
        parser.add_argument(
            "--all",
            action="store_true",
            dest="every_import",
            help="Every import of the season, not just the newest.",
        )

    def handle(self, *args, season=None, every_import=False, **options):
        season = self._season(season)
        if every_import:
            moments = list(
                PlayerSnapshot.objects.filter(season=season)
                .order_by("as_of")
                .values_list("as_of", flat=True)
                .distinct()
            )
        else:
            moments = [None]

        stored = 0
        for moment in moments:
            try:
                dream_team = refresh(season, moment)
            except NoDreamTeam as reason:
                label = _moment(moment) if moment else "newest import"
                self.stdout.write(f"{label}: no Dream Team. {reason}")
                continue
            stored += 1
            self.stdout.write(_line(dream_team))

        self.stdout.write(self.style.SUCCESS(f"{stored} Dream Team(s) stored for {season}."))

    def _season(self, label):
        if label:
            try:
                return Season.objects.get(label=label)
            except Season.DoesNotExist:
                raise CommandError(f"There is no season {label}.") from None
        season = Season.objects.filter(is_current=True).first()
        if season is None:
            raise CommandError("There is no current season. Pass --season.")
        return season


def _moment(value):
    return f"{timezone.localtime(value):%Y-%m-%d %H:%M}"


def _line(dream_team):
    result = changes(dream_team)
    if result.is_first:
        change = "first of the season"
    else:
        change = f"{len(result.came_in)} in, {len(result.went_out)} out"
    return (
        f"{_moment(dream_team.as_of)}: {dream_team.total_fp} points, "
        f"${dream_team.total_salary / 1_000_000:.2f}M ({change})"
    )
