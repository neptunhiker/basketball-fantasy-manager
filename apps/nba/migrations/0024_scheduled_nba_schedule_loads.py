"""Load the NBA schedule twice a year: before the season, and after the Cup.

1 October: the schedule is out by then, and nothing has been played. 20
December: the NBA Cup's group stage has ended and each team's last two games
are on the schedule. Six or seven requests each; the task skips without a
current season that is still going, so the October load waits for next
season to be set up -- run `import_nba_schedule` by hand once it is.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from django.db import migrations

FUNC = "apps.nba.tasks.scheduled_nba_schedule_load"
BERLIN = ZoneInfo("Europe/Berlin")
LOADS = [
    ("NBA schedule load: season start", "0 6 1 10 *", (10, 1)),
    ("NBA schedule load: after the NBA Cup", "0 6 20 12 *", (12, 20)),
]


def next_run(month, day):
    now = datetime.now(BERLIN)
    this_year = datetime(now.year, month, day, 6, tzinfo=BERLIN)
    return this_year if this_year > now else this_year.replace(year=now.year + 1)


def create_schedules(apps, schema_editor):
    Schedule = apps.get_model("django_q", "Schedule")
    for name, cron, (month, day) in LOADS:
        Schedule.objects.get_or_create(
            name=name,
            defaults={
                "func": FUNC,
                "schedule_type": "C",
                "cron": cron,
                "repeats": -1,
                "next_run": next_run(month, day),
            },
        )


def delete_schedules(apps, schema_editor):
    apps.get_model("django_q", "Schedule").objects.filter(func=FUNC).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("nba", "0023_game"),
        ("django_q", "0019_alter_task_options_alter_ormq_key_alter_ormq_lock_and_more"),
    ]

    operations = [
        migrations.RunPython(create_schedules, delete_schedules),
    ]
