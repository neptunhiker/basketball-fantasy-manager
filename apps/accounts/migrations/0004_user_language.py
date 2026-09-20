from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_english_user_labels"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="language",
            field=models.CharField(
                choices=[("en", "English"), ("de", "German")],
                default="en",
                max_length=10,
                verbose_name="Language",
            ),
        ),
    ]