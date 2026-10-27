"""Pull the RotoWire news feed every four hours.

Six requests a day, about 180 a month: well inside the free plan's 500. The
feed only ever shows its newest 25 items, so a longer gap risks missing some.
As with the basketball.de import, the schedule can be paused or retimed in the
admin afterwards.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.db import migrations

NAME = "RotoWire news import"
FUNC = "apps.nba.tasks.scheduled_rotowire_news_import"
CRON = "0 */4 * * *"
BERLIN = ZoneInfo("Europe/Berlin")


def next_four_hour_mark():
    # Without an explicit next_run Django-Q would fire the task on deploy.
    now = datetime.now(BERLIN).replace(minute=0, second=0, microsecond=0)
    return now + timedelta(hours=4 - now.hour % 4)


def create_schedule(apps, schema_editor):
    Schedule = apps.get_model("django_q", "Schedule")
    Schedule.objects.get_or_create(
        name=NAME,
        defaults={
            "func": FUNC,
            "schedule_type": "C",
            "cron": CRON,
            "repeats": -1,
            "next_run": next_four_hour_mark(),
        },
    )


def delete_schedule(apps, schema_editor):
    apps.get_model("django_q", "Schedule").objects.filter(name=NAME).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("nba", "0018_rotowire_news"),
        ("django_q", "0019_alter_task_options_alter_ormq_key_alter_ormq_lock_and_more"),
    ]

    operations = [
        migrations.RunPython(create_schedule, delete_schedule),
    ]
