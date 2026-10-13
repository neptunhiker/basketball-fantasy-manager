from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.nba.models import SeasonStatsImport
from apps.nba.season_stats import (
    SeasonStatsFileError,
    confirm_season_stats,
    discard_season_stats,
    preview_season_stats,
)


class Command(BaseCommand):
    help = (
        "Import official per-game season stats from an NBA.com CSV export and "
        "link every line to a player. For local databases; production uses the "
        "upload page."
    )

    def add_arguments(self, parser):
        parser.add_argument("path", help="The CSV file.")
        parser.add_argument("--season", required=True, help="Season label, e.g. 2025-26.")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Read and match the file and report the result, without saving any stats.",
        )

    def handle(self, *args, path, season, dry_run=False, **options):
        file = Path(path)
        if not file.is_file():
            raise CommandError(f"No such file: {file}")
        try:
            run = preview_season_stats(
                file.read_bytes(),
                season=season,
                file_name=file.name,
                source=SeasonStatsImport.Source.COMMAND,
            )
        except SeasonStatsFileError as exc:
            raise CommandError(str(exc)) from exc

        if dry_run:
            discard_season_stats(run)
        else:
            run = confirm_season_stats(run)
        self._report(run, dry_run)

    def _report(self, run, dry_run):
        prefix = "Dry run, nothing saved: " if dry_run else "Import finished: "
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}{run.season}, {run.rows} lines; {run.matched} matched, "
                f"{run.created} new, {run.updated} updated, {run.removed} removed."
            )
        )
        for title, lines in (
            ("Unmatched (not in our player list)", run.unmatched),
            ("Ambiguous (fix the name or add an alias)", run.ambiguous),
            ("Skipped lines", run.skipped),
            ("Warnings", run.warnings),
        ):
            if lines:
                self.stdout.write(self.style.WARNING(f"{title} ({len(lines)}):"))
                for line in lines:
                    self.stdout.write(f"  - {line}")
