"""When buying and selling become available for a season."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("fantasy", "0008_season_signings_close_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="season",
            name="signings_open_at",
            field=models.DateTimeField(
                blank=True,
                help_text="Before this moment rosters cannot be created and players cannot be signed. Leave empty to open immediately.",
                null=True,
                verbose_name="Signings open",
            ),
        ),
    ]