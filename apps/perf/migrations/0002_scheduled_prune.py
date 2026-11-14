"""Prune request metrics once a day, at 04:30 Berlin time.

Quiet hours, and clear of the basketball.de imports. Like the other schedules
it can be paused or retimed in the admin afterwards.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.db import migrations

NAME = "Prune request metrics"
FUNC = "apps.perf.tasks.prune_request_metrics"
CRON = "30 4 * * *"
BERLIN = ZoneInfo("Europe/Berlin")


def next_run():
    # Without an explicit next_run Django-Q would fire the task on deploy.
    now = datetime.now(BERLIN)
    run = now.replace(hour=4, minute=30, second=0, microsecond=0)
    return run if run > now else run + timedelta(days=1)


def create_schedule(apps, schema_editor):
    Schedule = apps.get_model("django_q", "Schedule")
    Schedule.objects.get_or_create(
        name=NAME,
        defaults={
            "func": FUNC,
            "schedule_type": "C",
            "cron": CRON,
            "repeats": -1,
            "next_run": next_run(),
        },
    )


def delete_schedule(apps, schema_editor):
    apps.get_model("django_q", "Schedule").objects.filter(name=NAME).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("perf", "0001_request_metric"),
        ("django_q", "0019_alter_task_options_alter_ormq_key_alter_ormq_lock_and_more"),
    ]

    operations = [
        migrations.RunPython(create_schedule, delete_schedule),
    ]
