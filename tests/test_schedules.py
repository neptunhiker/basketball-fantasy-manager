"""What every Django-Q schedule the migrations create needs to run at all."""

from django.utils import timezone
from django_q.models import Schedule


def test_every_schedule_can_work_out_its_next_run(db):
    # A cron schedule needs croniter. Without it the scheduler fails on every
    # tick, logs "Could not create task from schedule" and runs nothing.
    schedules = list(Schedule.objects.all())

    assert schedules
    for schedule in schedules:
        assert schedule.calculate_next_run() > timezone.now(), schedule.name
