"""Give each team the ID the schedule feed (ESPN's numbering) knows it by."""

from django.db import migrations, models

# A copy, not an import: a migration keeps the values it was written with.
ESPN_TEAM_IDS = {
    "ATL": 1,
    "BOS": 2,
    "NOP": 3,
    "CHI": 4,
    "CLE": 5,
    "DAL": 6,
    "DEN": 7,
    "DET": 8,
    "GSW": 9,
    "HOU": 10,
    "IND": 11,
    "LAC": 12,
    "LAL": 13,
    "MIA": 14,
    "MIL": 15,
    "MIN": 16,
    "BKN": 17,
    "NYK": 18,
    "ORL": 19,
    "PHI": 20,
    "PHX": 21,
    "POR": 22,
    "SAC": 23,
    "SAS": 24,
    "OKC": 25,
    "UTA": 26,
    "WAS": 27,
    "TOR": 28,
    "MEM": 29,
    "CHA": 30,
}


def fill_espn_ids(apps, schema_editor):
    Team = apps.get_model("nba", "Team")
    for abbreviation, espn_id in ESPN_TEAM_IDS.items():
        Team.objects.filter(abbreviation=abbreviation).update(espn_id=espn_id)


class Migration(migrations.Migration):
    dependencies = [
        ("nba", "0021_api_quota"),
    ]

    operations = [
        migrations.AddField(
            model_name="team",
            name="espn_id",
            field=models.PositiveSmallIntegerField(
                blank=True, null=True, unique=True, verbose_name="ESPN ID"
            ),
        ),
        migrations.RunPython(fill_espn_ids, migrations.RunPython.noop),
    ]
