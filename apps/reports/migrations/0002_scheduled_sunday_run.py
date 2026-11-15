"""Write and publish an AI trade report every Sunday at 12:30 Berlin time.

After the daily 12:15 stats import, and well before the 22:00 salary update
and trade processing. The task itself skips when the run is switched off on
the Reports page, outside the season, without an API key or past the monthly
spending limit, so the schedule can stay in place all year.
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db import migrations

NAME = "Sunday AI trade report"
FUNC = "apps.reports.tasks.scheduled_weekly_run"
CRON = "30 12 * * 0"
BERLIN = ZoneInfo("Europe/Berlin")


def next_sunday_midday():
    # Without an explicit next_run Django-Q would fire the task on deploy.
    now = datetime.now(BERLIN)
    sunday = now.date() + timedelta(days=(6 - now.weekday()) % 7)
    run = datetime.combine(sunday, time(12, 30), tzinfo=BERLIN)
    return run if run > now else run + timedelta(days=7)


def create_schedule(apps, schema_editor):
    Schedule = apps.get_model("django_q", "Schedule")
    Schedule.objects.get_or_create(
        name=NAME,
        defaults={
            "func": FUNC,
            "schedule_type": "C",
            "cron": CRON,
            "repeats": -1,
            "next_run": next_sunday_midday(),
        },
    )


def delete_schedule(apps, schema_editor):
    apps.get_model("django_q", "Schedule").objects.filter(name=NAME).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0001_initial"),
        ("django_q", "0019_alter_task_options_alter_ormq_key_alter_ormq_lock_and_more"),
    ]

    operations = [
        migrations.RunPython(create_schedule, delete_schedule),
    ]
