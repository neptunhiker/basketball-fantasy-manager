"""Pull the RotoWire news feed at 6:00, 12:00 and 18:00 instead of every four hours.

Three requests a day, about 93 a month. Django-Q reads the cron expression in
TIME_ZONE, so the times follow the Berlin clock through summer time.
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db import migrations

NAME = "RotoWire news import"
CRON = "0 6,12,18 * * *"
OLD_CRON = "0 */4 * * *"
HOURS = (6, 12, 18)
BERLIN = ZoneInfo("Europe/Berlin")


def next_pull():
    """The first of 6:00, 12:00 and 18:00 Berlin time still ahead."""
    now = datetime.now(BERLIN)
    for day in (now.date(), now.date() + timedelta(days=1)):
        for hour in HOURS:
            candidate = datetime.combine(day, time(hour), tzinfo=BERLIN)
            if candidate > now:
                return candidate
    raise AssertionError("unreachable")


def retime(cron, next_run):
    def apply(apps, schema_editor):
        apps.get_model("django_q", "Schedule").objects.filter(name=NAME).update(
            cron=cron, next_run=next_run()
        )

    return apply


def next_four_hour_mark():
    now = datetime.now(BERLIN).replace(minute=0, second=0, microsecond=0)
    return now + timedelta(hours=4 - now.hour % 4)


class Migration(migrations.Migration):
    dependencies = [
        ("nba", "0019_scheduled_rotowire_news_import"),
    ]

    operations = [
        migrations.RunPython(retime(CRON, next_pull), retime(OLD_CRON, next_four_hour_mark)),
    ]
