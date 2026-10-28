from django.core.management.base import BaseCommand, CommandError
from django.db.models import Max
from django.utils import timezone

from apps.nba.models import ApiQuota, Game
from apps.nba.schedule import current_season, load_season, ranges, season_span, why_not_now
from apps.nba.services import PROVIDER, NbaApiError


class Command(BaseCommand):
    help = "Load the current season's NBA schedule: every game, six or seven requests."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Load after the season has ended or below the monthly reserve.",
        )
        parser.add_argument(
            "--check",
            action="store_true",
            help="Only say whether a load would be made, and why not; makes no request.",
        )

    def handle(self, *args, force=False, check=False, **options):
        reason = why_not_now(force=force)
        if check:
            self._report(reason)
            return
        if reason:
            hint = " Run with --force to load anyway." if why_not_now(force=True) is None else ""
            raise CommandError(f"No load made: {reason}.{hint}")
        try:
            result = load_season()
        except NbaApiError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"NBA schedule loaded: {result}"))

    def _report(self, reason):
        season = current_season()
        if reason:
            self.stdout.write(f"No load would be made: {reason}.")
        else:
            requests = len(list(ranges(*season_span(season))))
            self.stdout.write(f"A load would be made: {requests} requests for {season.label}.")
        if season is not None:
            stored = Game.objects.filter(season_year=season.ends_on.year)
            last = stored.aggregate(last=Max("updated_at"))["last"]
            when = f", last loaded {timezone.localtime(last):%d.%m.%Y %H:%M}" if last else ""
            self.stdout.write(f"Stored: {stored.count()} games for {season.label}{when}.")
        provider_quota = ApiQuota.objects.filter(provider=PROVIDER).first()
        if provider_quota is None:
            self.stdout.write("RapidAPI has not reported a count yet.")
            return
        resets = (
            f", resets {timezone.localtime(provider_quota.resets_at):%d.%m.%Y %H:%M}"
            if provider_quota.resets_at
            else ""
        )
        reported = timezone.localtime(provider_quota.reported_at)
        self.stdout.write(
            f"RapidAPI: {provider_quota.remaining} of {provider_quota.limit} requests left"
            f"{resets} (as of {reported:%d.%m.%Y %H:%M})."
        )
