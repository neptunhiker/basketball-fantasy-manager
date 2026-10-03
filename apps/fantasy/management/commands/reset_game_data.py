"""Wipe rosters and player data, to start a season from a clean slate.

Meant for the moment before a new game begins: everything people played and
everything the import brought in goes, while the frame stays -- accounts,
manager profiles, teams, seasons and their trade grants, team notes. The next
basketball.de import then re-creates every player with their ID.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from apps.fantasy.models import PlayerSnapshot, Roster, RosterPlayer, Transaction, WatchlistEntry
from apps.nba.models import ImportRun, Player, PlayerInjury, PlayerNote

# In deletion order: whatever PROTECTs a player has to go before the players.
TARGETS = [
    ("Transactions", Transaction),
    ("Roster spots", RosterPlayer),
    ("Rosters", Roster),
    ("Watchlist entries", WatchlistEntry),
    ("Snapshots", PlayerSnapshot),
    ("Injuries", PlayerInjury),
    ("Player notes", PlayerNote),
    ("Players", Player),
    ("Import runs", ImportRun),
]


class Command(BaseCommand):
    help = (
        "Delete all rosters, transactions, watchlists, players, snapshots, injuries, "
        "player notes and import runs. Keeps users, managers, teams, seasons, trade "
        "grants and team notes. Asks for the database name to confirm."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true", help="Only show what would be deleted."
        )

    def handle(self, *args, dry_run=False, **options):
        database = connection.settings_dict["NAME"]
        counts = [(label, model.objects.count()) for label, model in TARGETS]

        self.stdout.write(f"Database: {database}")
        for label, count in counts:
            self.stdout.write(f"  {label:<18} {count:>7}")
        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run: nothing deleted."))
            return
        if not any(count for _, count in counts):
            self.stdout.write("Nothing to delete.")
            return

        answer = input(f'Type the database name "{database}" to delete all of this: ')
        if answer.strip() != database:
            raise CommandError("Not confirmed. Nothing was deleted.")

        with transaction.atomic():
            for _, model in TARGETS:
                model.objects.all().delete()
        self.stdout.write(self.style.SUCCESS("Deleted. Users, teams and seasons are untouched."))
