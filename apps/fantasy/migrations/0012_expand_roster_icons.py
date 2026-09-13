from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("fantasy", "0011_roster_icon"),
    ]

    operations = [
        migrations.AlterField(
            model_name="roster",
            name="icon",
            field=models.CharField(
                choices=[
                    ("bear", "Bear"),
                    ("bunny", "Bunny"),
                    ("croc", "Crocodile"),
                    ("deer", "Deer"),
                    ("elephant", "Elephant"),
                    ("fox", "Fox"),
                    ("fox_2", "Fox 2"),
                    ("giraffe", "Giraffe"),
                    ("koala", "Koala"),
                    ("lion", "Lion"),
                    ("monkey", "Monkey"),
                    ("owl", "Owl"),
                    ("panda", "Panda"),
                    ("racoon", "Racoon"),
                    ("seal", "Seal"),
                    ("sonic", "Sonic"),
                    ("squirrel", "Squirrel"),
                    ("tiger", "Tiger"),
                    ("wolf", "Wolf"),
                    ("zebra", "Zebra"),
                ],
                default="koala",
                max_length=32,
                verbose_name="Icon",
            ),
        ),
    ]