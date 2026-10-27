from django.core.management.base import BaseCommand, CommandError

from apps.nba.rotowire import sync_news
from apps.nba.services import NbaApiError


class Command(BaseCommand):
    help = "Fetch the RotoWire NBA news feed and store its new items."

    def handle(self, *args, **options):
        try:
            result = sync_news()
        except NbaApiError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"RotoWire news imported: {result}"))
