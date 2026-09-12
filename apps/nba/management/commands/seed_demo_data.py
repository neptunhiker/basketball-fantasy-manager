"""Fabricate a player pool big enough to build a legal roster against, plus
the weekly history the official game would have produced by now.

Refuses to run outside DEBUG. These are invented names with invented prices,
and the moment they reach a real database they are indistinguishable from
imported data.
"""

import datetime as dt
import random
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.fantasy.models import PlayerSnapshot, Season
from apps.fantasy.services import record_snapshot
from apps.nba.models import Player, Team

# The ten real players already in the dev database, with prices that put the
# stars where you would expect them.
KNOWN_SALARIES = {
    "nikola-jokic": 18_000_000,
    "shai-gilgeous-alexander": 17_500_000,
    "luka-doncic": 16_800_000,
    "giannis-antetokounmpo": 16_500_000,
    "victor-wembanyama": 15_900_000,
    "jayson-tatum": 14_200_000,
    "anthony-edwards": 13_800_000,
    "joel-embiid": 12_400_000,
    "stephen-curry": 12_100_000,
    "lebron-james": 9_600_000,
}

FIRST_NAMES = [
    "Marcus",
    "Tyrese",
    "Jalen",
    "Darius",
    "Malik",
    "Cameron",
    "Isaiah",
    "Trey",
    "Keegan",
    "Brandon",
    "Devin",
    "Jaden",
    "Corey",
    "Amari",
    "Elias",
    "Noah",
    "Julian",
    "Andre",
    "Terrance",
    "Quentin",
    "Bryce",
    "Dominic",
    "Rashad",
    "Kobe",
    "Xavier",
    "Miles",
    "Silas",
    "Omar",
    "Gideon",
    "Ravi",
]
LAST_NAMES = [
    "Whitfield",
    "Okonkwo",
    "Barnett",
    "Delgado",
    "Fournier",
    "Halstead",
    "Iversen",
    "Kowalski",
    "Lindqvist",
    "Mbaye",
    "Nakamura",
    "Oyelaran",
    "Petrov",
    "Quintero",
    "Rousseau",
    "Sandoval",
    "Thibault",
    "Ustinov",
    "Vandenberg",
    "Wexler",
    "Yilmaz",
    "Zabala",
    "Ashford",
    "Bricourt",
    "Castellan",
    "Devereux",
    "Eastwood",
    "Falkenrath",
    "Grimaldi",
    "Havelock",
]

# Roughly the shape of a real player pool, and comfortably enough of each
# position to satisfy the 5/5/2 minimums with choices left over.
POSITION_MIX = (
    [Player.Position.GUARD] * 12 + [Player.Position.FORWARD] * 12 + [Player.Position.CENTER] * 6
)

# Tiers rather than one distribution across the whole range. A uniform or
# triangular spread up to 18m puts the mean so high that the cheapest legal
# fifteen costs more than the cap, which makes the roster flow impossible to
# test -- the opposite of the point. Weighted toward the bottom, a legal roster
# is reachable with room to spare and upgrading into the stars is what breaks
# the budget.
SALARY_TIERS = [
    (0.30, 500_000, 1_500_000),  # fringe
    (0.35, 1_500_000, 3_500_000),  # rotation
    (0.25, 3_500_000, 7_000_000),  # starters
    (0.10, 7_000_000, 14_000_000),  # stars
]


MILLION = Decimal("1000000")

# The official game reprices everyone once a week, on a Sunday, so that is the
# state decisions are made against. Trading itself is not tied to a weekday.
SNAPSHOT_WEEKS = 6
SNAPSHOT_HOUR = 8  # local time: a Sunday morning scrape

# The import will not be that tidy, though, so neither is this. Scrapes that
# fall off the Sunday cadence, as (weeks after the first Sunday, days past that
# Sunday, hour, minute) -- declared rather than drawn at random, so that the
# demo history stays reproducible and the awkward cases are deliberate:
#
#   * a Wednesday evening and a Thursday morning, somebody running the import
#     mid-week to see where a price had got to
#   * the same Sunday scraped twice, 41 minutes apart, which is what a retry
#     after a timeout looks like from the outside
#
# Anything on or after the final Sunday is dropped: the series has to end on
# that Sunday, because it is the snapshot every current price is derived from.
EXTRA_SCRAPES = (
    (0, 3, 21, 30),  # Wednesday evening, week 1
    (2, 0, 8, 41),  # the third Sunday, for the second time
    (2, 4, 7, 15),  # Thursday morning, week 3
    (4, 3, 19, 45),  # Wednesday evening, week 5
)

# Games in a week. A rest week from the second Sunday on, so that not everyone
# has played the same number of games and the per-game figures are comparisons
# between unequal sample sizes -- which is the situation the real data is in.
WEEKLY_GAMES = (2, 4)
REST_WEEK_CHANCE = 0.12

# Fantasy points per game, modelled as a floor plus a slope on salary. The
# official game prices players off their production, so uncorrelated demo
# numbers would make every future value screen pure noise -- but the scatter is
# wide on purpose, because finding the players whose price and production
# disagree is the entire point of this app.
FP_BASE = Decimal("6")
FP_PER_MILLION = Decimal("2.4")
FP_SCATTER = (0.7, 1.3)

# How much a single week's form swings around a player's own level, and how
# hard the price reacts to it. Prices move far less than form does: a good week
# nudges a salary, it does not double it.
FORM_SCATTER = (0.55, 1.45)
PRICE_SENSITIVITY = Decimal("0.09")
PRICE_FLOOR = Decimal("300000")


def fabricate_salary(rng):
    roll = rng.random()
    cumulative = 0.0
    for share, low, high in SALARY_TIERS:
        cumulative += share
        if roll <= cumulative:
            return Decimal(round(rng.uniform(low, high), -4))
    return Decimal(round(rng.uniform(*SALARY_TIERS[-1][1:]), -4))


def snapshot_sundays(season, weeks=SNAPSHOT_WEEKS):
    """The first `weeks` Sunday snapshots of a season, oldest first.

    Anchored to the season rather than to today: the 2026-27 season tips off in
    October, so a history dated "the last six weeks" would sit entirely before
    the season it belongs to -- something the real import can never produce.
    """
    first = season.starts_on + dt.timedelta(days=(6 - season.starts_on.weekday()) % 7)
    return [
        timezone.make_aware(
            dt.datetime.combine(first + dt.timedelta(weeks=week), dt.time(SNAPSHOT_HOUR))
        )
        for week in range(weeks)
    ]


def snapshot_schedule(season, weeks=SNAPSHOT_WEEKS):
    """Every moment the imaginary scraper ran, oldest first.

    The Sundays plus `EXTRA_SCRAPES`. A perfectly regular history never
    exercises the screens that have to cope with an irregular one, and the
    real import will be irregular from its first failed run onwards.
    """
    sundays = snapshot_sundays(season, weeks)
    extras = {
        timezone.make_aware(
            dt.datetime.combine(
                sundays[week].date() + dt.timedelta(days=days), dt.time(hour, minute)
            )
        )
        for week, days, hour, minute in EXTRA_SCRAPES
        if week < len(sundays)
    }
    return sorted(set(sundays) | {when for when in extras if sundays[0] < when < sundays[-1]})


def expand_to_schedule(weekly, sundays, schedule):
    """The weekly series read at every moment in `schedule`.

    Off-cadence scrapes are interpolated from the two Sundays around them
    rather than fabricated in their own right, and that is what keeps the
    series consistent with itself: the Sundays come out identical to what this
    command has always written, and nothing in between can contradict them.

    Two modelling choices, both of them the dull answer on purpose.

    Salary holds at the last Sunday's figure, because the official game
    reprices once a week -- a Wednesday scrape is still reading Sunday's price,
    and the mid-week rows say so by showing no change at all.

    Points are shared out by games played, not by elapsed time. Three days into
    a week in which the player got on court once, the scrape shows one game and
    the points from it, not three sevenths of the week. That also means a
    second scrape 41 minutes after the first repeats it exactly -- nothing
    happened in between, so nothing may appear to have.
    """
    by_moment = dict(zip(sundays, weekly, strict=True))
    rows = []

    for when in schedule:
        if when in by_moment:
            rows.append((when, *by_moment[when]))
            continue

        # Strictly inside the window, so there is always a Sunday on each side.
        index = max(i for i, sunday in enumerate(sundays) if sunday <= when)
        games, points, salary = weekly[index]
        next_games, next_points, _ = weekly[index + 1]

        span = (sundays[index + 1] - sundays[index]).total_seconds()
        share = Decimal(str((when - sundays[index]).total_seconds() / span))
        in_the_week = next_games - games
        played = int((share * in_the_week).to_integral_value())
        if played:
            points += ((next_points - points) * played / in_the_week).quantize(Decimal("0.01"))
            games += played

        rows.append((when, games, points, salary))

    return rows


def fabricate_history(rng, final_salary, weeks):
    """One player's season to date: `(games, total_fp, salary)` per week.

    Cumulative and chronological. Games and points only ever grow, because
    that is what a season total does -- a snapshot series where they fall would
    be a scraping bug, not a slump.

    The salary series is built *backwards* from `final_salary`, the price we
    already show for this player today, so nothing on any existing screen
    moves. The weeks before it follow from the form he showed: a hot week means
    he was cheaper the Sunday before, which is exactly the pattern the value
    screens are meant to find.
    """
    level = (FP_BASE + FP_PER_MILLION * final_salary / MILLION) * Decimal(
        str(round(rng.uniform(*FP_SCATTER), 3))
    )

    games, points = 0, Decimal("0")
    weekly = []
    for week in range(weeks):
        resting = week > 0 and rng.random() < REST_WEEK_CHANCE
        played = 0 if resting else rng.randint(*WEEKLY_GAMES)
        # A rest week is neutral for the price: no games is no evidence, not
        # evidence of decline.
        form = Decimal("1") if resting else Decimal(str(round(rng.uniform(*FORM_SCATTER), 3)))

        games += played
        points += (level * form * played).quantize(Decimal("0.01"))
        weekly.append((games, points, form))

    salaries = [None] * weeks
    salaries[-1] = final_salary
    for week in range(weeks - 1, 0, -1):
        move = Decimal("1") + PRICE_SENSITIVITY * (weekly[week][2] - Decimal("1"))
        earlier = Decimal(round(salaries[week] / move, -4))
        salaries[week - 1] = max(PRICE_FLOOR, earlier)

    return [
        (games, points, salary) for (games, points, _), salary in zip(weekly, salaries, strict=True)
    ]


class Command(BaseCommand):
    help = "Create a fabricated player pool with a weekly history. Development only."

    def add_arguments(self, parser):
        parser.add_argument("--seed", type=int, default=20262027, help="RNG seed.")
        parser.add_argument(
            "--weeks",
            type=int,
            default=SNAPSHOT_WEEKS,
            help=f"How many Sundays to simulate (default: {SNAPSHOT_WEEKS}).",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete every snapshot first, including other seasons'.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Nur mit DEBUG=True -- das sind erfundene Daten.")

        season = Season.objects.filter(is_current=True).first()
        if season is None:
            raise CommandError("No current season. Create one first.")
        if not Team.objects.exists():
            raise CommandError("No teams. Run `manage.py seed_teams` first.")

        weeks = options["weeks"]
        if weeks < 1:
            raise CommandError("--weeks muss mindestens 1 sein.")
        sundays = snapshot_sundays(season, weeks)
        schedule = snapshot_schedule(season, weeks)

        if options["reset"]:
            deleted, _ = PlayerSnapshot.objects.all().delete()
            # The cached columns go with them: they are a copy of the newest
            # snapshot, and without one there is nothing for them to mirror.
            Player.objects.update(
                current_salary=None,
                current_total_fp=None,
                current_fp_per_game=None,
                current_games_played=None,
            )
            self.stdout.write(f"{deleted} snapshots deleted.")

        # This command owns the demo history of the current season, so anything
        # off its own schedule is a leftover from an earlier shape of this data
        # rather than something worth keeping.
        stale, _ = PlayerSnapshot.objects.filter(season=season).exclude(as_of__in=schedule).delete()
        if stale:
            self.stdout.write(f"{stale} snapshots outside the schedule removed.")

        rng = random.Random(options["seed"])
        # A separate stream for the scoring, so that adding it did not shuffle
        # the salaries the first stream already produced: re-running the command
        # leaves every price exactly where it was.
        scoring_rng = random.Random(options["seed"] + 1)
        teams = list(Team.objects.order_by("abbreviation"))
        created = 0

        # The price each player ends the series on. Drawn first, for every
        # player, because the weekly history is derived backwards from it.
        # Fabricated players are spread evenly across all 30 franchises.
        anchors = {}
        for index, position in enumerate(POSITION_MIX):
            first = FIRST_NAMES[index % len(FIRST_NAMES)]
            last = LAST_NAMES[(index * 7) % len(LAST_NAMES)]
            player, was_created = Player.objects.get_or_create(
                first_name=first,
                last_name=last,
                defaults={"position": position, "team": teams[index % len(teams)]},
            )
            created += was_created
            anchors[player] = fabricate_salary(rng)

        # The real names keep hand-picked prices so the top of the market looks
        # like the top of the market.
        for slug, salary in KNOWN_SALARIES.items():
            player = Player.objects.filter(slug=slug).first()
            if player is not None:
                anchors[player] = Decimal(salary)

        for player, final_salary in anchors.items():
            weekly = fabricate_history(scoring_rng, final_salary, weeks)
            for as_of, games, total_fp, salary in expand_to_schedule(weekly, sundays, schedule):
                record_snapshot(
                    player,
                    season,
                    as_of,
                    salary=salary,
                    total_fp=total_fp,
                    games_played=games,
                    team=player.team,
                    position=player.position,
                )

        counts = {
            label: Player.objects.filter(position=value).count()
            for value, label in Player.Position.choices
        }
        total = Player.objects.count()
        cheapest = Player.objects.order_by("current_salary").first()
        dearest = Player.objects.order_by("-current_salary").first()

        self.stdout.write(
            self.style.SUCCESS(
                f"{created} new players, {len(anchors) * len(schedule)} snapshots "
                f"written ({weeks} Sundays plus {len(schedule) - weeks} mid-week reads, "
                f"{sundays[0]:%b %-d} to {sundays[-1]:%b %-d, %Y}).\n"
                f"Pool: {total} players ({', '.join(f'{v}x {k}' for k, v in counts.items())}).\n"
                f"Current prices from {cheapest.current_salary:,.0f} ({cheapest}) "
                f"to {dearest.current_salary:,.0f} ({dearest})."
            )
        )
