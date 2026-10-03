from getpass import getpass

from django.core.management.base import BaseCommand, CommandError

from apps.nba.bbde import BbdeError
from apps.nba.importer import import_bbde
from apps.nba.models import ImportRun


class Command(BaseCommand):
    help = (
        "Import salaries and fantasy points from the basketball.de player list. "
        "Asks for the password; it is never stored."
    )

    def add_arguments(self, parser):
        parser.add_argument("--username", help="basketball.de username (asked for if omitted).")
        # Deliberately no --password: a password on the command line ends up in
        # the shell history and in the process list.
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Do everything, report the result, then roll every change back.",
        )

    def handle(self, *args, username=None, dry_run=False, **options):
        username = username or input("basketball.de username: ").strip()
        password = getpass("basketball.de password: ")
        if not username or not password:
            raise CommandError("A username and a password are required.")

        try:
            run = import_bbde(username, password, source=ImportRun.Source.COMMAND, dry_run=dry_run)
        except BbdeError as exc:
            raise CommandError(str(exc)) from exc

        self._report(run)

    def _report(self, run):
        prefix = "Dry run, nothing saved: " if run.dry_run else "Import finished: "
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}{run.pages} pages, {run.rows} players; {run.matched} matched, "
                f"{run.created} created, {run.reactivated} reactivated, "
                f"{run.inactivated} marked inactive."
            )
        )
        for title, lines in (
            ("Created", run.created_players),
            ("Ambiguous (link these by hand in the admin)", run.ambiguous),
            ("Skipped", run.skipped),
            ("Warnings", run.warnings),
        ):
            if lines:
                self.stdout.write(self.style.WARNING(f"{title} ({len(lines)}):"))
                for line in lines:
                    self.stdout.write(f"  - {line}")
