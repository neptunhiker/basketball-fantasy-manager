"""`Manager`, and the room to move rosters onto it.

Split from the backfill on purpose. This migration only widens the schema, so
it is safe to run and trivial to reverse; 0006 moves the data and 0007 closes
the door behind it.

`Roster.owner` is relaxed to nullable here rather than in 0007, which looks like
the wrong place until you reverse the chain: on the way back out, 0007 restores
the column before 0006 has refilled it. If the column were only made nullable
in 0007, that restore would be a non-null column over empty rows and Postgres
would refuse. Relaxing it before the backfill is what makes the round trip work.
"""

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("fantasy", "0004_english_labels"),
    ]

    operations = [
        migrations.CreateModel(
            name="Manager",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("nick_name", models.CharField(max_length=40, verbose_name="Nickname")),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="manager_profiles",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="User",
                    ),
                ),
            ],
            options={
                "verbose_name": "Manager",
                "verbose_name_plural": "Managers",
                "ordering": ["nick_name"],
            },
        ),
        migrations.AddConstraint(
            model_name="manager",
            constraint=models.UniqueConstraint(
                fields=("user", "nick_name"), name="unique_nickname_per_user"
            ),
        ),
        # Nullable for now: there is nothing to point at until 0006 has run.
        migrations.AddField(
            model_name="roster",
            name="manager",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="rosters",
                to="fantasy.manager",
                verbose_name="Manager",
            ),
        ),
        # See the module docstring: this is here for the sake of the reverse.
        migrations.AlterField(
            model_name="roster",
            name="owner",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="rosters",
                to=settings.AUTH_USER_MODEL,
                verbose_name="Owner",
            ),
        ),
    ]
