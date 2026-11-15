"""Start an AI report run from the shell: the same as the Reports page's button."""

from django.core.management.base import BaseCommand, CommandError

from apps.fantasy.models import Roster, Season
from apps.reports import catalog, costs, inputs, services
from apps.reports.models import ReportRun, ReportSettings


class Command(BaseCommand):
    help = (
        "Start an AI report run for the current season. Asks before spending money unless "
        "--yes is given. Locally (Django-Q in sync mode) it runs right away; on a server "
        "the worker picks it up."
    )

    def add_arguments(self, parser):
        parser.add_argument("--model", choices=list(catalog.APPROVED), help="Default: settings.")
        parser.add_argument("--effort", choices=catalog.EFFORTS, help="Default: settings.")
        parser.add_argument("--label", default="")
        parser.add_argument("--briefing-only", action="store_true")
        parser.add_argument(
            "--roster", action="append", default=[], help="Only this roster (id). Repeatable."
        )
        parser.add_argument("--yes", action="store_true", help="Do not ask before starting.")

    def handle(self, *args, **options):
        season = Season.objects.filter(is_current=True).first()
        if season is None:
            raise CommandError("There is no current season.")
        report_settings = ReportSettings.load()
        model = options["model"] or report_settings.default_model
        effort = options["effort"] or report_settings.default_effort
        rosters = None
        if options["roster"]:
            rosters = Roster.objects.filter(season=season, pk__in=options["roster"])

        if not options["yes"]:
            try:
                preview = inputs.build(
                    season,
                    []
                    if options["briefing_only"]
                    else rosters
                    if rosters is not None
                    else Roster.objects.filter(season=season, is_active=True),
                )
            except inputs.NoReportData as error:
                raise CommandError(str(error)) from error
            estimate = costs.estimate_run(
                model,
                effort,
                preview.shared_input,
                [entry.own_input for entry in preview.rosters],
            )
            answer = input(
                f"{catalog.display_name(model)} at {effort} effort, "
                f"{len(preview.rosters)} rosters, about ${estimate:.2f}. Start? [y/N] "
            )
            if answer.strip().lower() not in ("y", "yes"):
                self.stdout.write("Not started.")
                return

        try:
            run = services.start_run(
                season,
                model=model,
                effort=effort,
                trigger=ReportRun.Trigger.MANUAL,
                label=options["label"],
                briefing_only=options["briefing_only"],
                rosters=rosters,
            )
        except services.ReportsUnavailable as error:
            raise CommandError(str(error)) from error

        run.refresh_from_db()
        self.stdout.write(f"Run {run.pk}: {run.get_status_display()}")
        for report in run.reports.select_related("roster"):
            who = report.roster or report.get_kind_display()
            line = f"  {who}: {report.status}, ${report.cost_usd}"
            if report.error:
                line += f" -- {report.error}"
            self.stdout.write(line)
