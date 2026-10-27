"""Run the basketball.de import every day at 12:15 Berlin time.

The schedule is created here so every environment has it after `migrate`;
its time can be changed or the schedule paused in the admin afterwards.
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db import migrations, models

NAME = "Daily basketball.de import"
FUNC = "apps.nba.tasks.scheduled_bbde_import"
CRON = "15 12 * * *"
BERLIN = ZoneInfo("Europe/Berlin")


def next_quarter_past_noon():
    # Without an explicit next_run Django-Q would fire the task on deploy.
    now = datetime.now(BERLIN)
    today = datetime.combine(now.date(), time(12, 15), tzinfo=BERLIN)
    return today if today > now else datetime.combine(
        now.date() + timedelta(days=1), time(12, 15), tzinfo=BERLIN
    )


def create_schedule(apps, schema_editor):
    Schedule = apps.get_model("django_q", "Schedule")
    Schedule.objects.get_or_create(
        name=NAME,
        defaults={
            "func": FUNC,
            "schedule_type": "C",
            "cron": CRON,
            "repeats": -1,
            "next_run": next_quarter_past_noon(),
        },
    )


def delete_schedule(apps, schema_editor):
    apps.get_model("django_q", "Schedule").objects.filter(name=NAME).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("nba", "0016_drop_tfp"),
        ("django_q", "0019_alter_task_options_alter_ormq_key_alter_ormq_lock_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="importrun",
            name="source",
            field=models.CharField(
                choices=[("web", "Web"), ("command", "Command"), ("scheduled", "Scheduled")],
                max_length=16,
                verbose_name="Source",
            ),
        ),
        migrations.RunPython(create_schedule, delete_schedule),
    ]
