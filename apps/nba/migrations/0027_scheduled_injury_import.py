"""Import NBA injuries every day at 12:20 Berlin time.

Right after the 12:15 snapshot, so the Sunday 12:30 AI report reads that day's
injuries. One RapidAPI request a day. The task skips outside the season, and
the schedule can be paused or retimed in the admin afterwards.
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db import migrations

NAME = "Daily injury import"
FUNC = "apps.nba.tasks.scheduled_injury_import"
CRON = "20 12 * * *"
BERLIN = ZoneInfo("Europe/Berlin")


def next_twelve_twenty():
    # Without an explicit next_run Django-Q would fire the task on deploy.
    now = datetime.now(BERLIN)
    run = datetime.combine(now.date(), time(12, 20), tzinfo=BERLIN)
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
            "next_run": next_twelve_twenty(),
        },
    )


def delete_schedule(apps, schema_editor):
    apps.get_model("django_q", "Schedule").objects.filter(name=NAME).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("nba", "0026_player_hotness"),
        ("django_q", "0019_alter_task_options_alter_ormq_key_alter_ormq_lock_and_more"),
    ]

    operations = [
        migrations.RunPython(create_schedule, delete_schedule),
    ]
