"""Print what an AI report run would send, without sending anything.

The place to judge a report's quality before paying for one: everything the
AI will know is in this text.
"""

from django.core.management.base import BaseCommand, CommandError

from apps.fantasy.models import Roster, Season
from apps.reports import catalog, costs, inputs


class Command(BaseCommand):
    help = "Print the shared input and each roster's input of a report run. Calls no API."

    def add_arguments(self, parser):
        parser.add_argument(
            "--roster",
            action="append",
            default=[],
            help="Only this roster (id). Repeat for several. Default: every active roster.",
        )
        parser.add_argument(
            "--summary", action="store_true", help="Only the sizes and estimated costs."
        )

    def handle(self, *args, roster=(), summary=False, **options):
        season = Season.objects.filter(is_current=True).first()
        if season is None:
            raise CommandError("There is no current season.")
        rosters = Roster.objects.filter(season=season)
        rosters = rosters.filter(pk__in=roster) if roster else rosters.filter(is_active=True)
        try:
            built = inputs.build(season, rosters)
        except inputs.NoReportData as error:
            raise CommandError(str(error)) from error

        if not summary:
            self.stdout.write(built.shared_input)
            for entry in built.rosters:
                self.stdout.write(f"\n===== {entry.roster} ({entry.roster.pk}) =====\n")
                self.stdout.write(entry.own_input)

        own = [entry.own_input for entry in built.rosters]
        self.stdout.write("\n===== Size and estimated cost =====")
        self.stdout.write(
            f"Shared input: {len(built.shared_input):,} characters, "
            f"~{costs.estimate_tokens(built.shared_input):,} tokens; "
            f"{len(built.player_refs)} players; {len(own)} rosters"
        )
        for model in catalog.MODELS:
            estimates = ", ".join(
                f"{effort} ${costs.estimate_run(model.id, effort, built.shared_input, own):.2f}"
                for effort in catalog.EFFORTS
            )
            self.stdout.write(f"  {model.name:<14} {estimates}")
