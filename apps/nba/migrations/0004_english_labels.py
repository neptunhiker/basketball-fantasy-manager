"""Field labels and model names, translated to English.

State only -- `verbose_name` lives in Django's migration graph but never in the
database, so this migration issues no SQL. It exists so `makemigrations --check`
stays quiet.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('nba', '0003_player_current_fp_per_game_and_more'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='player',
            options={'ordering': ['last_name', 'first_name'], 'verbose_name': 'Player', 'verbose_name_plural': 'Players'},
        ),
        migrations.AlterField(
            model_name='player',
            name='current_fp_per_game',
            field=models.DecimalField(blank=True, decimal_places=2, help_text='Empty until the player has appeared in a game.', max_digits=8, null=True, verbose_name='Points per game'),
        ),
        migrations.AlterField(
            model_name='player',
            name='current_games_played',
            field=models.PositiveSmallIntegerField(blank=True, help_text='Games played, from the newest snapshot.', null=True, verbose_name='Games played'),
        ),
        migrations.AlterField(
            model_name='player',
            name='current_salary',
            field=models.DecimalField(blank=True, decimal_places=2, help_text='Mirrored from the newest snapshot. Empty until there is one.', max_digits=12, null=True, verbose_name='Current salary'),
        ),
        migrations.AlterField(
            model_name='player',
            name='current_total_fp',
            field=models.DecimalField(blank=True, decimal_places=2, help_text='Points this season, from the newest snapshot.', max_digits=8, null=True, verbose_name='Fantasy points'),
        ),
        migrations.AlterField(
            model_name='player',
            name='first_name',
            field=models.CharField(blank=True, max_length=64, verbose_name='First name'),
        ),
        migrations.AlterField(
            model_name='player',
            name='is_active',
            field=models.BooleanField(default=True, help_text='Cleared by the import when a player stops appearing in the source.', verbose_name='Active'),
        ),
        migrations.AlterField(
            model_name='player',
            name='last_name',
            field=models.CharField(max_length=64, verbose_name='Last name'),
        ),
        migrations.AlterField(
            model_name='team',
            name='abbreviation',
            field=models.CharField(max_length=4, unique=True, verbose_name='Abbreviation'),
        ),
    ]
