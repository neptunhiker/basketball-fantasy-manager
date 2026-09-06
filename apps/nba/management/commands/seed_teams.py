"""Seed the 30 NBA franchises.

Teams are the one piece of reference data that almost never changes -- the last
relocation was in 2008 -- so they are worth hardcoding rather than scraping.
Everything downstream needs them to exist before it can import a single player.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.nba.models import Team

E = Team.Conference.EAST
W = Team.Conference.WEST
D = Team.Division

# (name, abbreviation, conference, division)
TEAMS = [
    # Eastern Conference -- Atlantic
    ("Boston Celtics", "BOS", E, D.ATLANTIC),
    ("Brooklyn Nets", "BKN", E, D.ATLANTIC),
    ("New York Knicks", "NYK", E, D.ATLANTIC),
    ("Philadelphia 76ers", "PHI", E, D.ATLANTIC),
    ("Toronto Raptors", "TOR", E, D.ATLANTIC),
    # Eastern Conference -- Central
    ("Chicago Bulls", "CHI", E, D.CENTRAL),
    ("Cleveland Cavaliers", "CLE", E, D.CENTRAL),
    ("Detroit Pistons", "DET", E, D.CENTRAL),
    ("Indiana Pacers", "IND", E, D.CENTRAL),
    ("Milwaukee Bucks", "MIL", E, D.CENTRAL),
    # Eastern Conference -- Southeast
    ("Atlanta Hawks", "ATL", E, D.SOUTHEAST),
    ("Charlotte Hornets", "CHA", E, D.SOUTHEAST),
    ("Miami Heat", "MIA", E, D.SOUTHEAST),
    ("Orlando Magic", "ORL", E, D.SOUTHEAST),
    ("Washington Wizards", "WAS", E, D.SOUTHEAST),
    # Western Conference -- Northwest
    ("Denver Nuggets", "DEN", W, D.NORTHWEST),
    ("Minnesota Timberwolves", "MIN", W, D.NORTHWEST),
    ("Oklahoma City Thunder", "OKC", W, D.NORTHWEST),
    ("Portland Trail Blazers", "POR", W, D.NORTHWEST),
    ("Utah Jazz", "UTA", W, D.NORTHWEST),
    # Western Conference -- Pacific
    ("Golden State Warriors", "GSW", W, D.PACIFIC),
    ("LA Clippers", "LAC", W, D.PACIFIC),
    ("Los Angeles Lakers", "LAL", W, D.PACIFIC),
    ("Phoenix Suns", "PHX", W, D.PACIFIC),
    ("Sacramento Kings", "SAC", W, D.PACIFIC),
    # Western Conference -- Southwest
    ("Dallas Mavericks", "DAL", W, D.SOUTHWEST),
    ("Houston Rockets", "HOU", W, D.SOUTHWEST),
    ("Memphis Grizzlies", "MEM", W, D.SOUTHWEST),
    ("New Orleans Pelicans", "NOP", W, D.SOUTHWEST),
    ("San Antonio Spurs", "SAS", W, D.SOUTHWEST),
]


class Command(BaseCommand):
    help = "Create or update the 30 NBA teams. Safe to run repeatedly."

    @transaction.atomic
    def handle(self, *args, **options):
        created = updated = 0

        for name, abbreviation, conference, division in TEAMS:
            # Keyed on the abbreviation because it is the one identifier that
            # outlives a rename -- the Bobcats became the Hornets, CHA did not.
            _, was_created = Team.objects.update_or_create(
                abbreviation=abbreviation,
                defaults={
                    "name": name,
                    "conference": conference,
                    "division": division,
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1

        self.stdout.write(self.style.SUCCESS(f"{created} Teams angelegt, {updated} aktualisiert."))
