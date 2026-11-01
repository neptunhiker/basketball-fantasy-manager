"""Run the basketball.de import every Sunday at 22:15 Berlin time.

The official game reprices every Sunday at 22:00. The task itself skips
before the current season's trade buying opens -- the first salary update --
and after the season ends, so the schedule can run all year.
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db import migrations

NAME = "Sunday basketball.de salary import"
FUNC = "apps.nba.tasks.scheduled_salary_import"
CRON = "15 22 * * 0"
BERLIN = ZoneInfo("Europe/Berlin")


def next_sunday_evening():
    # Without an explicit next_run Django-Q would fire the task on deploy.
    now = datetime.now(BERLIN)
    sunday = now.date() + timedelta(days=(6 - now.weekday()) % 7)
    run = datetime.combine(sunday, time(22, 15), tzinfo=BERLIN)
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
            "next_run": next_sunday_evening(),
        },
    )


def delete_schedule(apps, schema_editor):
    apps.get_model("django_q", "Schedule").objects.filter(name=NAME).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("nba", "0024_scheduled_nba_schedule_loads"),
        ("django_q", "0019_alter_task_options_alter_ormq_key_alter_ormq_lock_and_more"),
    ]

    operations = [
        migrations.RunPython(create_schedule, delete_schedule),
    ]
