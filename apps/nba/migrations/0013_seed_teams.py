"""Create the 30 NBA teams, so every database has them after `migrate`.

Without teams the basketball.de import cannot place a single player on a
team. The list is copied here on purpose rather than imported from the
`seed_teams` command: a migration has to keep doing what it did on the day it
was written, whatever the command looks like later.
"""

from django.db import migrations

# (name, abbreviation, conference, division)
TEAMS = [
    ("Boston Celtics", "BOS", "EAST", "ATLANTIC"),
    ("Brooklyn Nets", "BKN", "EAST", "ATLANTIC"),
    ("New York Knicks", "NYK", "EAST", "ATLANTIC"),
    ("Philadelphia 76ers", "PHI", "EAST", "ATLANTIC"),
    ("Toronto Raptors", "TOR", "EAST", "ATLANTIC"),
    ("Chicago Bulls", "CHI", "EAST", "CENTRAL"),
    ("Cleveland Cavaliers", "CLE", "EAST", "CENTRAL"),
    ("Detroit Pistons", "DET", "EAST", "CENTRAL"),
    ("Indiana Pacers", "IND", "EAST", "CENTRAL"),
    ("Milwaukee Bucks", "MIL", "EAST", "CENTRAL"),
    ("Atlanta Hawks", "ATL", "EAST", "SOUTHEAST"),
    ("Charlotte Hornets", "CHA", "EAST", "SOUTHEAST"),
    ("Miami Heat", "MIA", "EAST", "SOUTHEAST"),
    ("Orlando Magic", "ORL", "EAST", "SOUTHEAST"),
    ("Washington Wizards", "WAS", "EAST", "SOUTHEAST"),
    ("Denver Nuggets", "DEN", "WEST", "NORTHWEST"),
    ("Minnesota Timberwolves", "MIN", "WEST", "NORTHWEST"),
    ("Oklahoma City Thunder", "OKC", "WEST", "NORTHWEST"),
    ("Portland Trail Blazers", "POR", "WEST", "NORTHWEST"),
    ("Utah Jazz", "UTA", "WEST", "NORTHWEST"),
    ("Golden State Warriors", "GSW", "WEST", "PACIFIC"),
    ("LA Clippers", "LAC", "WEST", "PACIFIC"),
    ("Los Angeles Lakers", "LAL", "WEST", "PACIFIC"),
    ("Phoenix Suns", "PHX", "WEST", "PACIFIC"),
    ("Sacramento Kings", "SAC", "WEST", "PACIFIC"),
    ("Dallas Mavericks", "DAL", "WEST", "SOUTHWEST"),
    ("Houston Rockets", "HOU", "WEST", "SOUTHWEST"),
    ("Memphis Grizzlies", "MEM", "WEST", "SOUTHWEST"),
    ("New Orleans Pelicans", "NOP", "WEST", "SOUTHWEST"),
    ("San Antonio Spurs", "SAS", "WEST", "SOUTHWEST"),
]


def create_teams(apps, schema_editor):
    Team = apps.get_model("nba", "Team")
    for name, abbreviation, conference, division in TEAMS:
        # Keyed on the abbreviation, like `seed_teams`: databases that were
        # seeded already keep their rows and only get the names brought in line.
        Team.objects.update_or_create(
            abbreviation=abbreviation,
            defaults={"name": name, "conference": conference, "division": division},
        )


class Migration(migrations.Migration):
    dependencies = [
        ("nba", "0012_bbde_import"),
    ]

    operations = [
        migrations.RunPython(create_teams, migrations.RunPython.noop),
    ]
