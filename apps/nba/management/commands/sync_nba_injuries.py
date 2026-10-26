from django.core.management.base import BaseCommand, CommandError

from apps.nba.services import NbaApiError, daily_limit_for, sync_injuries


class Command(BaseCommand):
    help = "Fetch NBA injuries and synchronize them to local players."

    def handle(self, *args, **options):
        try:
            summary = sync_injuries(daily_limit=daily_limit_for(None))
        except NbaApiError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            self.style.SUCCESS(
                "NBA injuries synchronized: "
                f"{summary['created']} created, {summary['updated']} updated, "
                f"{summary['recovered']} recovered, {summary['unmatched']} unmatched, "
                f"{summary['ambiguous']} ambiguous."
            )
        )
