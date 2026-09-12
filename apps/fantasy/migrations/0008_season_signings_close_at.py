"""When buying and selling stop for a season.

Nullable with no default, so every season that already exists stays open and
nothing in flight is affected. A season closes only once somebody sets a
moment on it.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("fantasy", "0007_roster_manager_required"),
    ]

    operations = [
        migrations.AddField(
            model_name="season",
            name="signings_close_at",
            field=models.DateTimeField(
                blank=True,
                help_text="After this moment a roster can only change through trades: no more buying, no more selling, and no new rosters. Leave empty to keep signings open for the whole season.",
                null=True,
                verbose_name="Signings close",
            ),
        ),
    ]
