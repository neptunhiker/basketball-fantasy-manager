from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("fantasy", "0009_season_signings_open_at"),
    ]

    operations = [
        migrations.CreateModel(
            name="WatchlistEntry",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "player",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="watchlist_entries",
                        to="nba.player",
                        verbose_name="Player",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="watchlist_entries",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="User",
                    ),
                ),
            ],
            options={
                "verbose_name": "Watchlist entry",
                "verbose_name_plural": "Watchlist entries",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="watchlistentry",
            constraint=models.UniqueConstraint(
                fields=("user", "player"),
                name="one_watchlist_entry_per_user_player",
            ),
        ),
    ]