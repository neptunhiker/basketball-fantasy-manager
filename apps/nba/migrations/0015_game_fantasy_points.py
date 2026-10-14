from decimal import Decimal

from django.db import migrations, models


def game_fantasy_points(stats):
    """Frozen copy of apps.nba.season_stats.game_fantasy_points.

    A migration must give the same result whenever it runs, so it does not
    import the app's formula, which may change later.
    """
    missed_field_goals = max(stats.field_goals_attempted - stats.field_goals_made, Decimal(0))
    missed_free_throws = max(stats.free_throws_attempted - stats.free_throws_made, Decimal(0))
    points = (
        stats.points
        + stats.rebounds
        + Decimal("1.5") * stats.assists
        + 2 * stats.steals
        + 2 * stats.blocks
        - 2 * stats.turnovers
        - Decimal("0.5") * missed_field_goals
        - Decimal("0.5") * missed_free_throws
        - Decimal("0.5") * stats.personal_fouls
    )
    return points.quantize(Decimal("0.01"))


def fill(apps, schema_editor):
    PlayerSeasonStats = apps.get_model("nba", "PlayerSeasonStats")
    rows = list(PlayerSeasonStats.objects.all())
    for stats in rows:
        stats.game_fantasy_points = game_fantasy_points(stats)
    PlayerSeasonStats.objects.bulk_update(rows, ["game_fantasy_points"], batch_size=500)


class Migration(migrations.Migration):

    dependencies = [
        ("nba", "0014_season_stats"),
    ]

    operations = [
        migrations.AddField(
            model_name="playerseasonstats",
            name="game_fantasy_points",
            field=models.DecimalField(
                decimal_places=2, max_digits=5, null=True, verbose_name="Fantasy points"
            ),
        ),
        migrations.RunPython(fill, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="playerseasonstats",
            name="game_fantasy_points",
            field=models.DecimalField(
                decimal_places=2, max_digits=5, verbose_name="Fantasy points"
            ),
        ),
    ]
