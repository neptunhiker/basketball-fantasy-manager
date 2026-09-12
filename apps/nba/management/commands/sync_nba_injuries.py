from django.core.management.base import BaseCommand, CommandError

from apps.nba.services import NbaApiError, sync_injuries


class Command(BaseCommand):
    help = "Fetch NBA injuries and synchronize them to local players."

    def handle(self, *args, **options):
        try:
            summary = sync_injuries()
        except NbaApiError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            self.style.SUCCESS(
                "NBA injuries synchronized: "
                f"{summary['created']} created, {summary['updated']} updated, "
                f"{summary['unmatched']} unmatched, {summary['ambiguous']} ambiguous."
            )
        )