"""Field labels and model names, translated to English.

State only -- `verbose_name` lives in Django's migration graph but never in the
database, so this migration issues no SQL. It exists so `makemigrations --check`
stays quiet.
"""

import django.db.models.deletion
import django.db.models.expressions
import django.db.models.functions.comparison
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('fantasy', '0003_transaction_prices'),
        ('nba', '0004_english_labels'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='playersnapshot',
            options={'ordering': ['-as_of', 'player'], 'verbose_name': 'Player snapshot', 'verbose_name_plural': 'Player snapshots'},
        ),
        migrations.AlterModelOptions(
            name='roster',
            options={'ordering': ['name'], 'verbose_name': 'Roster', 'verbose_name_plural': 'Rosters'},
        ),
        migrations.AlterModelOptions(
            name='rosterplayer',
            options={'ordering': ['-added_at'], 'verbose_name': 'Roster spell', 'verbose_name_plural': 'Roster spells'},
        ),
        migrations.AlterModelOptions(
            name='season',
            options={'ordering': ['-starts_on'], 'verbose_name': 'Season', 'verbose_name_plural': 'Seasons'},
        ),
        migrations.AlterModelOptions(
            name='transaction',
            options={'ordering': ['-occurred_at'], 'verbose_name': 'Transaction', 'verbose_name_plural': 'Transactions'},
        ),
        migrations.AlterField(
            model_name='playersnapshot',
            name='as_of',
            field=models.DateTimeField(verbose_name='As of'),
        ),
        migrations.AlterField(
            model_name='playersnapshot',
            name='fp_per_game',
            field=models.GeneratedField(db_persist=True, expression=django.db.models.expressions.CombinedExpression(models.F('total_fp'), '/', django.db.models.functions.comparison.NullIf(models.F('games_played'), 0)), output_field=models.DecimalField(decimal_places=2, max_digits=8), verbose_name='Points per game'),
        ),
        migrations.AlterField(
            model_name='playersnapshot',
            name='games_played',
            field=models.PositiveSmallIntegerField(verbose_name='Games played'),
        ),
        migrations.AlterField(
            model_name='playersnapshot',
            name='player',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='snapshots', to='nba.player', verbose_name='Player'),
        ),
        migrations.AlterField(
            model_name='playersnapshot',
            name='salary',
            field=models.DecimalField(decimal_places=2, max_digits=12, verbose_name='Salary'),
        ),
        migrations.AlterField(
            model_name='playersnapshot',
            name='season',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='snapshots', to='fantasy.season', verbose_name='Season'),
        ),
        migrations.AlterField(
            model_name='playersnapshot',
            name='team',
            field=models.ForeignKey(blank=True, help_text='The team the source reported for this player at that moment.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='player_snapshots', to='nba.team', verbose_name='Team'),
        ),
        migrations.AlterField(
            model_name='playersnapshot',
            name='total_fp',
            field=models.DecimalField(decimal_places=2, max_digits=8, verbose_name='Fantasy points'),
        ),
        migrations.AlterField(
            model_name='roster',
            name='cash',
            field=models.DecimalField(decimal_places=2, default=Decimal('60000000'), max_digits=12, verbose_name='Cash'),
        ),
        migrations.AlterField(
            model_name='roster',
            name='owner',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='rosters', to=settings.AUTH_USER_MODEL, verbose_name='Owner'),
        ),
        migrations.AlterField(
            model_name='roster',
            name='season',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='rosters', to='fantasy.season', verbose_name='Season'),
        ),
        migrations.AlterField(
            model_name='rosterplayer',
            name='added_at',
            field=models.DateTimeField(verbose_name='Added'),
        ),
        migrations.AlterField(
            model_name='rosterplayer',
            name='player',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='roster_memberships', to='nba.player', verbose_name='Player'),
        ),
        migrations.AlterField(
            model_name='rosterplayer',
            name='removed_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Removed'),
        ),
        migrations.AlterField(
            model_name='rosterplayer',
            name='roster',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='memberships', to='fantasy.roster', verbose_name='Roster'),
        ),
        migrations.AlterField(
            model_name='season',
            name='ends_on',
            field=models.DateField(verbose_name='Ends on'),
        ),
        migrations.AlterField(
            model_name='season',
            name='is_current',
            field=models.BooleanField(default=False, verbose_name='Current'),
        ),
        migrations.AlterField(
            model_name='season',
            name='label',
            field=models.CharField(max_length=16, unique=True, verbose_name='Label'),
        ),
        migrations.AlterField(
            model_name='season',
            name='starts_on',
            field=models.DateField(verbose_name='Starts on'),
        ),
        migrations.AlterField(
            model_name='transaction',
            name='cash_delta',
            field=models.DecimalField(decimal_places=2, max_digits=12, verbose_name='Cash flow'),
        ),
        migrations.AlterField(
            model_name='transaction',
            name='note',
            field=models.CharField(blank=True, max_length=200, verbose_name='Note'),
        ),
        migrations.AlterField(
            model_name='transaction',
            name='occurred_at',
            field=models.DateTimeField(verbose_name='Occurred at'),
        ),
        migrations.AlterField(
            model_name='transaction',
            name='player_in',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='transactions_in', to='nba.player', verbose_name='Player in'),
        ),
        migrations.AlterField(
            model_name='transaction',
            name='player_out',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='transactions_out', to='nba.player', verbose_name='Player out'),
        ),
        migrations.AlterField(
            model_name='transaction',
            name='price_in',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True, verbose_name='Price in'),
        ),
        migrations.AlterField(
            model_name='transaction',
            name='price_out',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True, verbose_name='Price out'),
        ),
        migrations.AlterField(
            model_name='transaction',
            name='roster',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='transactions', to='fantasy.roster', verbose_name='Roster'),
        ),
    ]
