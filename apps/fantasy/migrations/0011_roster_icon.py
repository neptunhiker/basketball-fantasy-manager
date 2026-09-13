from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("fantasy", "0010_watchlistentry"),
    ]

    operations = [
        migrations.AddField(
            model_name="roster",
            name="icon",
            field=models.CharField(
                choices=[
                    ("koala", "Koala"),
                    ("giraffe", "Giraffe"),
                    ("seal", "Seal"),
                    ("monkey", "Monkey"),
                    ("racoon", "Racoon"),
                    ("croc", "Crocodile"),
                    ("zebra", "Zebra"),
                    ("sonic", "Sonic"),
                    ("elephant", "Elephant"),
                    ("deer", "Deer"),
                ],
                default="koala",
                max_length=32,
                verbose_name="Icon",
            ),
        ),
    ]