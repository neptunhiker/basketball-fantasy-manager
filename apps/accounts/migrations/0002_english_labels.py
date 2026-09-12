"""Field labels and model names, translated to English.

State only -- `verbose_name` lives in Django's migration graph but never in the
database, so this migration issues no SQL. It exists so `makemigrations --check`
stays quiet.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0001_initial'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='user',
            options={'ordering': ['email'], 'verbose_name': 'User', 'verbose_name_plural': 'Users'},
        ),
        migrations.AlterField(
            model_name='user',
            name='is_active',
            field=models.BooleanField(default=True, help_text='Deactivate instead of deleting to lock an account out.', verbose_name='aktiv'),
        ),
        migrations.AlterField(
            model_name='user',
            name='is_staff',
            field=models.BooleanField(default=False, help_text='Whether this person may sign in to the admin site.', verbose_name='Team-Mitglied'),
        ),
    ]
