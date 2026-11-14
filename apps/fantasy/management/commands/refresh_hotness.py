from django.core.management.base import BaseCommand

from apps.fantasy.services import refresh_all_hotness


class Command(BaseCommand):
    help = (
        "Recount every player's stored hotness from his snapshots. Needed only "
        "after writes that skip post_save, such as loaddata or a bulk insert."
    )

    def handle(self, *args, **options):
        changed = refresh_all_hotness()
        self.stdout.write(self.style.SUCCESS(f"Hotness refreshed; {changed} player(s) changed."))
