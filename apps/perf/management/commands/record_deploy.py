"""Record that a commit just went live, for the dashboard's deploy markers.

`scripts/deploy.sh` runs this once the new containers pass their health
check, so a deploy that failed is never marked. By hand, after deploying some
other way:

    python manage.py record_deploy -- "$(git rev-parse HEAD)" "$(git log -1 --format=%s)"
"""

import re

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.perf.models import Deploy

SHA = re.compile(r"[0-9a-f]{7,40}")


class Command(BaseCommand):
    help = "Record a deploy of the given commit, now. Does nothing if it is already the latest."

    def add_arguments(self, parser):
        parser.add_argument("sha", help="The commit that went live (7 to 40 hex digits).")
        parser.add_argument("subject", nargs="?", default="", help="The commit's first line.")

    def handle(self, *args, sha, subject, **options):
        sha = sha.strip().lower()
        if not SHA.fullmatch(sha):
            raise CommandError(f"Not a commit hash: {sha!r}")
        subject = " ".join(subject.split())[:300]

        # Running the deploy again for the same commit is not a new deploy.
        latest = Deploy.objects.first()
        if latest and (latest.sha.startswith(sha) or sha.startswith(latest.sha)):
            self.stdout.write(f"{latest.short_sha} is already the latest deploy, nothing recorded.")
            return

        deploy = Deploy.objects.create(deployed_at=timezone.now(), sha=sha, subject=subject)
        self.stdout.write(self.style.SUCCESS(f"Recorded deploy {deploy}"))
