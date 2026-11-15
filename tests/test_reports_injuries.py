"""Injuries as stories for the AI: `apps.reports.injuries` and its input blocks."""

import datetime as dt
from decimal import Decimal
from itertools import count

import pytest

from apps.nba.models import Game, PlayerInjury, PlayerNews
from apps.reports import inputs
from apps.reports.history import GameLog
from apps.reports.injuries import build
from tests.reports_factories import at, make_player, make_season, make_team, snapshot

pytestmark = pytest.mark.django_db

NOW = at(11, 15, 12, 30)
LATEST = at(11, 15, 12, 15)
_ids = count(1)


@pytest.fixture
def season():
    return make_season()


@pytest.fixture
def team():
    team, other = make_team("CHI"), make_team("BOS")
    # One game every day from Nov 1 to Nov 14, at 20:00 (UTC: before the next snapshot).
    for day in range(1, 15):
        Game.objects.create(
            espn_id=f"chi-{day}",
            season_year=2027,
            tip_off=at(11, day, 20),
            home_team=team,
            away_team=other,
        )
    return team


def report(player, day, status="Out", returns=None, hour=12, **extra):
    return PlayerInjury.objects.create(
        player=player,
        observed_at=at(11, day, hour, 20),
        status=status,
        return_date=returns,
        **extra,
    )


def games(season, player, per_game, days, base_day):
    """A baseline snapshot on `base_day`, then one game on each day in `days`."""
    snapshot(season, player, at(11, base_day, 12, 15), total=0, games=0)
    for played, day in enumerate(days, start=1):
        snapshot(
            season, player, at(11, day, 12, 15), total=Decimal(per_game) * played, games=played
        )


def spells_for(season, players):
    log = GameLog(season, [player.pk for player in players], until=LATEST)
    return build(season, players, log, LATEST, NOW)


def test_out_since_is_the_first_report_of_the_unbroken_spell(season, team):
    who = make_player(team=team)
    report(who, 1, status="Healthy")
    report(who, 5, returns=dt.date(2026, 11, 10))
    report(who, 6, returns=dt.date(2026, 11, 10))
    report(who, 9, returns=dt.date(2026, 11, 18))
    report(who, 13, status="Out", returns=dt.date(2026, 11, 24))

    (spell,) = spells_for(season, [who]).hurt

    assert spell.out_since == at(11, 5, 12, 20)
    # Repeats are left out: the start, then the two moves of the return date.
    assert [(c.on.day, c.return_date.day) for c in spell.history] == [(5, 10), (9, 18), (13, 24)]
    assert spell.history[1].return_changed and not spell.history[1].status_changed


def test_team_games_missed_leave_out_games_he_played(season, team):
    who = make_player(team=team)
    report(who, 5, status="Day-To-Day")
    # Team games after Nov 5 12:20 up to now: Nov 5..14 = 10. He played 6 of them.
    games(season, who, 20, range(6, 12), base_day=5)

    (spell,) = spells_for(season, [who]).hurt

    assert spell.games_missed == 4


def test_history_keeps_the_last_three_changes_within_21_days(season, team):
    who = make_player(team=team)
    for day, back in ((1, 3), (2, 4), (3, 5), (4, 6), (5, 7)):
        report(who, day, returns=dt.date(2026, 11, back))

    (spell,) = spells_for(season, [who]).hurt

    assert [change.on.day for change in spell.history] == [1, 3, 4, 5]


def test_a_player_back_within_14_days_is_listed_as_returned(season, team):
    back = make_player(team=team)
    report(back, 3)
    report(back, 9, status="Healthy")
    long_ago = make_player(team=team)
    PlayerInjury.objects.create(player=long_ago, observed_at=at(10, 20), status="Out")
    PlayerInjury.objects.create(player=long_ago, observed_at=at(10, 25), status="Healthy")

    built = spells_for(season, [back, long_ago])

    assert built.hurt == []
    (spell,) = built.returned
    assert spell.player == back
    assert spell.returned_on == dt.date(2026, 11, 9)
    # Team games after Nov 3 12:20 until Nov 9 12:20: Nov 3..8.
    assert spell.games_missed == 6


def test_injuries_are_read_up_to_the_run_not_the_snapshot(season, team):
    who = make_player(team=team)
    # The 12:20 import, after the 12:15 snapshot and before the 12:30 run.
    report(who, 15, hour=12)

    built = spells_for(season, [who])

    assert [spell.player for spell in built.hurt] == [who]
    assert built.as_of == at(11, 15, 12, 20)


def test_the_newest_injury_headline_of_the_week_is_attached(season, team):
    who = make_player(team=team)
    report(who, 14)
    for day, kind, headline in (
        (5, PlayerNews.Kind.INJURY, "Too old"),
        (12, PlayerNews.Kind.INJURY, "Out two more weeks"),
        (13, PlayerNews.Kind.NEWS, "Not an injury item"),
    ):
        PlayerNews.objects.create(
            rotowire_news_id=next(_ids),
            player=who,
            kind=kind,
            feed_player_name=who.full_name,
            headline=headline,
            url="https://example.com",
            published_on=dt.date(2026, 11, day),
        )

    (spell,) = spells_for(season, [who]).hurt

    assert spell.headline == (dt.date(2026, 11, 12), "Out two more weeks")


def test_comments_are_kept_for_players_out_and_cut_short(season, team):
    star = make_player(team=team, fp_per_game="30")
    bench = make_player(team=team, fp_per_game="8")
    report(star, 14, short_comment="word " * 100)
    report(bench, 14, status="Day-To-Day", short_comment="Questionable for Monday.")

    by_player = {spell.player: spell for spell in spells_for(season, [star, bench]).hurt}

    assert len(by_player[star].comment) == 180 and by_player[star].comment.endswith("…")
    assert by_player[bench].comment == ""


# --- who benefits --------------------------------------------------------------


def absence(season, team, *, fp_per_game="30", position="C", returns=None):
    star = make_player(position, team=team, fp_per_game=fp_per_game)
    report(star, 7, returns=returns)
    return star


def teammate(season, team, before, since, position="C"):
    """Five games at `before` points (Nov 2-6), then games at `since` (Nov 8-12)."""
    mate = make_player(position, team=team, fp_per_game=str(before))
    snapshot(season, mate, at(11, 1, 12, 15), total=0, games=0)
    total, played = Decimal(0), 0
    for day in range(2, 13):
        if day == 7:
            continue
        total += Decimal(before if day < 7 else since)
        played += 1
        snapshot(season, mate, at(11, day, 12, 15), total=total, games=played)
    return mate


def test_teammates_who_rose_since_a_long_absence_are_named(season, team):
    star = absence(season, team)
    backup = teammate(season, team, before=10, since=22)
    guard = teammate(season, team, before=15, since=30, position="G")
    worse = teammate(season, team, before=20, since=15)

    (benefit,) = spells_for(season, [star, backup, guard, worse]).benefits

    # Same position first, then the biggest rise; nobody who fell.
    assert [gain.player for gain in benefit.teammates] == [backup, guard]
    gain = benefit.teammates[0]
    assert (gain.before.per_game, gain.before.games) == (Decimal(10), 5)
    assert (gain.since.per_game, gain.since.games) == (Decimal(22), 5)


def test_short_absences_and_low_scorers_have_no_block(season, team):
    soon_back = absence(season, team, returns=dt.date(2026, 11, 16))
    bench = absence(season, team, fp_per_game="10")
    teammate(season, team, before=10, since=22)

    benefits = spells_for(season, [soon_back, bench]).benefits

    # soon_back has missed 8 games, so he counts; the bench player never does.
    assert [benefit.spell.player for benefit in benefits] == [soon_back]


def test_a_return_soon_and_few_games_missed_is_not_long(season, team):
    star = make_player("C", team=team, fp_per_game="30")
    report(star, 13, returns=dt.date(2026, 11, 18))

    assert spells_for(season, [star]).benefits == []


def test_other_absences_on_the_team_are_named(season, team):
    star = absence(season, team)
    other = absence(season, team, fp_per_game="12", position="G")
    backup = teammate(season, team, before=10, since=22)

    built = spells_for(season, [star, other, backup])

    (benefit,) = built.benefits
    assert benefit.also_out == [other]
    assert backup.pk in built.openings


def test_a_teammate_traded_in_counts_only_his_games_for_the_team(season, team):
    star = absence(season, team)
    newcomer = make_player("C", team=team)
    elsewhere = make_team("NYK")
    for day, total, games_played, at_team in (
        (1, 0, 0, elsewhere),
        (3, 40, 2, elsewhere),
        (9, 60, 3, team),
        (11, 80, 4, team),
    ):
        made = snapshot(season, newcomer, at(11, day, 12, 15), total=total, games=games_played)
        made.team = at_team
        made.save(update_fields=["team"])

    # No games for this team before the absence: nothing to compare.
    assert spells_for(season, [star, newcomer]).benefits[0].teammates == []


# --- the blocks the AI reads --------------------------------------------------------


def test_the_input_tells_the_story(season, team):
    star = absence(season, team, returns=dt.date(2026, 11, 24))
    star.last_name = "Smith"
    star.save()
    backup = teammate(season, team, before=10, since=22)
    snapshot(season, star, LATEST)
    snapshot(season, backup, LATEST)
    back = make_player("G", team=team, last_name="Back")
    snapshot(season, back, LATEST)
    report(back, 3)
    report(back, 10, status="Healthy")

    shared = inputs.build(season, now=NOW).shared_input

    block = shared.split("\nINJURIES", 1)[1].split("\nWHO BENEFITS", 1)[0]
    assert "injury reports as of 2026-11-10 12:20" in block
    assert "Pat Smith | CHI | C | Out | - | 2026-11-07 (8) | 2026-11-24 |" in block
    assert "RETURNED IN THE LAST 14 DAYS" in block
    assert "Pat Back | CHI | G | 2026-11-10 | 2026-11-03 (7) |" in block
    benefits = shared.split("\nWHO BENEFITS", 1)[1].split("\nSCHEDULE", 1)[0]
    assert "CHI without" in benefits and "Pat Smith (Out since 2026-11-07, 8 team games" in benefits
    assert "FP/game 10.0 (5 games) before -> 22.0 (5 games) since" in benefits
    assert "| back 2026-11-10" in shared  # the player table's injury column


def test_one_query_more_or_less_whatever_the_injuries(season, team, django_assert_max_num_queries):
    players = [absence(season, team) for _ in range(6)]
    log = GameLog(season, [player.pk for player in players], until=LATEST)

    with django_assert_max_num_queries(4):
        build(season, players, log, LATEST, NOW)
