"""Delete the rows the dashboard recorded about itself.

From now on `/performance/` is in `PERF_IGNORED_PATHS`. The visits recorded
before that would otherwise keep their percentile queries in the 7- and
30-day figures until they age out.
"""

from django.db import migrations

ROUTE = "perf:dashboard"


def drop_dashboard_metrics(apps, schema_editor):
    apps.get_model("perf", "RequestMetric").objects.filter(route=ROUTE).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("perf", "0002_scheduled_prune"),
    ]

    operations = [
        # Nothing to put back: they were measurements of the dashboard itself.
        migrations.RunPython(drop_dashboard_metrics, migrations.RunPython.noop),
    ]
