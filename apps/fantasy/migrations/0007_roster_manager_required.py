"""Close the door: `manager` becomes required and `owner` goes away.

The operations are spelled out rather than left to the autodetector because the
old constraint names `owner`, so it has to be dropped before the column can be.

Reversible, and tested to be: `owner` comes back nullable and empty here, 0006
refills it from the managers, and 0005 makes it required again. Each step is
wrong on its own and the three together are right, which is the reason the
nullability lives in 0005 and not in this file.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("fantasy", "0006_backfill_managers"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="roster",
            name="unique_roster_name_per_season",
        ),
        migrations.AlterField(
            model_name="roster",
            name="manager",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="rosters",
                to="fantasy.manager",
                verbose_name="Manager",
            ),
        ),
        migrations.RemoveField(
            model_name="roster",
            name="owner",
        ),
        # Scoped to the manager now, not the account: two of one user's profiles
        # may each keep a "Roster 1", which is the point of having profiles.
        migrations.AddConstraint(
            model_name="roster",
            constraint=models.UniqueConstraint(
                fields=("manager", "season", "name"),
                name="unique_roster_name_per_manager_and_season",
            ),
        ),
    ]
