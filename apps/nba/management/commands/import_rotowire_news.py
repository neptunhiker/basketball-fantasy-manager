from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.nba.models import ApiQuota
from apps.nba.rotowire import PROVIDER, sync_news, why_not_now
from apps.nba.services import NbaApiError


class Command(BaseCommand):
    help = "Fetch the RotoWire NBA news feed and store its new items."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Pull outside the season window or below the monthly reserve.",
        )
        parser.add_argument(
            "--check",
            action="store_true",
            help="Only say whether a pull would be made, and why not; makes no request.",
        )

    def handle(self, *args, force=False, check=False, **options):
        reason = why_not_now(force=force)
        if check:
            self._report(reason)
            return
        if reason:
            hint = " Run with --force to pull anyway." if why_not_now(force=True) is None else ""
            raise CommandError(f"No pull made: {reason}.{hint}")
        try:
            result = sync_news()
        except NbaApiError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"RotoWire news imported: {result}"))

    def _report(self, reason):
        if reason:
            self.stdout.write(f"No pull would be made: {reason}.")
        else:
            self.stdout.write("A pull would be made.")
        quota = ApiQuota.objects.filter(provider=PROVIDER).first()
        if quota is None:
            self.stdout.write("RapidAPI has not reported a count yet.")
            return
        resets = (
            f", resets {timezone.localtime(quota.resets_at):%d.%m.%Y %H:%M}"
            if quota.resets_at
            else ""
        )
        reported = timezone.localtime(quota.reported_at)
        self.stdout.write(
            f"RapidAPI: {quota.remaining} of {quota.limit} requests left{resets} "
            f"(as of {reported:%d.%m.%Y %H:%M})."
        )
