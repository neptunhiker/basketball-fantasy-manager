"""The roster timeline: fifteen spots, cut by trades, from the starting squad on.

Rows are written directly rather than through `services`, because the dates
are the point of every test here and the services stamp them with `now`.
"""

import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.fantasy.models import PlayerSnapshot, Roster, RosterPlayer, Season, Transaction
from apps.fantasy.timeline import roster_timeline
from apps.nba.models import Player

BERLIN = ZoneInfo("Europe/Berlin")


def at(month, day, hour=22, minute=0, year=None):
    year = year or (2026 if month >= 7 else 2027)
    return dt.datetime(year, month, day, hour, minute, tzinfo=BERLIN)


SIGNINGS_CLOSE = at(10, 20, 20, 30)
NOW = at(12, 1, 12)


@pytest.fixture
def season(db):
    return Season.objects.create(
        label="2026-27",
        starts_on=dt.date(2026, 10, 21),
        ends_on=dt.date(2027, 4, 11),
        is_current=True,
        signings_close_at=SIGNINGS_CLOSE,
        trade_market_opens_at=at(11, 15),
    )


@pytest.fixture
def roster(season, manager):
    return Roster.objects.create(manager=manager, season=season, name="Koalas")


_names = iter(f"Player{i:02d}" for i in range(1000))


def player(position="G", last_name=None):
    return Player.objects.create(
        first_name="Ann",
        last_name=last_name or next(_names),
        position=position,
        current_salary=Decimal("3000000"),
    )


def spell(roster, who, added, removed=None):
    return RosterPlayer.objects.create(
        roster=roster, player=who, added_at=added, removed_at=removed
    )


def squad(roster, positions="GGGGGGFFFFFFCCC"):
    """A starting fifteen, all signed well before the cutoff."""
    players = [player(pos) for pos in positions]
    for who in players:
        spell(roster, who, at(10, 10, 12))
    return players


def trade(roster, out, incoming, when, price_out="3000000", price_in="2500000"):
    RosterPlayer.objects.filter(roster=roster, player=out, removed_at__isnull=True).update(
        removed_at=when
    )
    spell(roster, incoming, when)
    return Transaction.objects.create(
        roster=roster,
        occurred_at=when,
        kind=Transaction.Kind.TRADE,
        player_out=out,
        player_in=incoming,
        price_out=Decimal(price_out),
        price_in=Decimal(price_in),
        cash_delta=Decimal(price_out) - Decimal(price_in),
    )


def names(timeline):
    return [[stint["name"] for stint in spot["stints"]] for spot in timeline.spots]


def test_there_is_no_timeline_before_signings_close(roster):
    squad(roster)
    assert roster_timeline(roster, now=at(10, 19, 12)) is None


def test_the_starting_squad_is_the_roster_when_signings_closed(roster):
    players = squad(roster, "GGGGGGFFFFFFCC")
    tried = player("C", "Tried")
    spell(roster, tried, at(10, 5, 8), removed=at(10, 17, 19, 49))
    # Sold and bought back the same evening: two spells, one player.
    back = player("C", "Back")
    spell(roster, back, at(10, 10, 22), removed=at(10, 17, 20, 25))
    spell(roster, back, at(10, 17, 20, 32))

    timeline = roster_timeline(roster, now=NOW)

    flat = [name for spot in names(timeline) for name in spot]
    assert len(timeline.spots) == 15
    assert "Ann Tried" not in flat
    assert flat.count("Ann Back") == 1
    assert {p.full_name for p in players} <= set(flat)
    # Every starter runs from the first day of the chart.
    assert all(spot["stints"][0]["start"] is None for spot in timeline.spots)
    assert timeline.starts_on == dt.date(2026, 10, 20)


def test_a_trade_cuts_a_spot_and_hands_it_on(roster):
    players = squad(roster)
    newcomer = player("G", "Newcomer")
    trade(roster, players[0], newcomer, at(11, 3))

    timeline = roster_timeline(roster, now=NOW)

    assert len(timeline.spots) == 15
    spot = next(s for s in timeline.spots if len(s["stints"]) == 2)
    before, after = spot["stints"]
    assert before["name"] == players[0].full_name
    assert before["end"] == "2026-11-03"
    assert before["left"]["partner"] == "Ann Newcomer"
    assert before["left"]["price"] == "$3.00M"
    assert after["start"] == "2026-11-03"
    assert after["end"] is None
    assert after["came"]["partner"] == players[0].full_name
    assert after["came"]["price"] == "$2.50M"
    assert timeline.trade_count == 1


def test_trades_on_one_day_and_chains_stay_in_their_spots(roster):
    players = squad(roster)
    first, second, third = player("G"), player("F"), player("G")
    trade(roster, players[0], first, at(11, 16))
    trade(roster, players[6], second, at(11, 16))
    trade(roster, first, third, at(11, 23))

    timeline = roster_timeline(roster, now=NOW)

    assert len(timeline.spots) == 15
    lanes = names(timeline)
    assert [players[0].full_name, first.full_name, third.full_name] in lanes
    assert [players[6].full_name, second.full_name] in lanes


def test_a_player_who_comes_back_returns_to_his_spot(roster):
    players = squad(roster)
    stand_in = player("G")
    trade(roster, players[0], stand_in, at(11, 3))
    trade(roster, stand_in, players[0], at(11, 20))

    timeline = roster_timeline(roster, now=NOW)

    assert [players[0].full_name, stand_in.full_name, players[0].full_name] in names(timeline)


def test_a_position_change_is_allowed_and_spots_sort_by_who_holds_them(roster):
    players = squad(roster, "GGGGGGFFFFFFCCC")
    center = player("C", "Center")
    trade(roster, players[0], center, at(11, 16))

    timeline = roster_timeline(roster, now=NOW)

    current = [spot["stints"][-1]["pos"] for spot in timeline.spots]
    assert current == sorted(current, key="GFC".index)
    assert timeline.spots[-1]["stints"][-1]["name"] in {
        "Ann Center",
        *(p.full_name for p in players[12:]),
    }


def snapshot(who, season, when, total):
    PlayerSnapshot.objects.create(
        player=who,
        season=season,
        as_of=when,
        salary=Decimal("3000000"),
        total_fp=Decimal(total),
        games_played=1,
        position=who.position,
    )


def test_points_are_what_he_scored_while_on_the_roster(roster, season):
    players = squad(roster)
    newcomer, unknown = player("G"), player("F")
    trade(roster, players[0], newcomer, at(11, 3))
    trade(roster, players[6], unknown, at(11, 10))
    snapshot(players[0], season, at(11, 1, 12), "90")
    snapshot(players[0], season, at(11, 3, 12), "120.5")
    snapshot(newcomer, season, at(11, 1, 12), "100")
    snapshot(newcomer, season, at(11, 20, 12), "160")
    snapshot(newcomer, season, at(12, 1, 11), "251")
    # His first snapshot comes after he arrived mid-season: no number at all.
    snapshot(unknown, season, at(11, 20, 12), "300")

    timeline = roster_timeline(roster, now=NOW)

    by_name = {s["name"]: s for spot in timeline.spots for s in spot["stints"]}
    assert by_name[players[0].full_name]["points"] == "120"  # from zero at the start
    assert by_name[newcomer.full_name]["points"] == "151"
    assert by_name[unknown.full_name]["points"] is None
    assert by_name[players[1].full_name]["points"] is None  # no snapshots at all


def count_queries(roster):
    with CaptureQueriesContext(connection) as queries:
        roster_timeline(roster, now=NOW)
    return len(queries)


def test_the_query_count_does_not_grow_with_the_trades(roster):
    players = squad(roster)
    roster = Roster.objects.select_related("season").get(pk=roster.pk)
    few = count_queries(roster)
    for day, out in zip(range(16, 30), players, strict=False):
        trade(roster, out, player(out.position), at(11, day))
    assert count_queries(roster) == few


# --- the history page ---------------------------------------------------------


@pytest.fixture
def signed_in(client, user, password, db):
    client.login(email=user.email, password=password)
    return client


def history_url(roster):
    return reverse("fantasy:roster-history", args=[roster.pk])


def test_the_history_page_carries_the_timeline_once_signings_closed(signed_in, roster):
    squad(roster)

    response = signed_in.get(history_url(roster))

    timeline = response.context["timeline"]
    assert len(timeline["spots"]) == 15
    assert timeline["startsOn"] == "2026-10-20"
    assert timeline["tradeFreeUntil"] == "2026-11-15"


def test_the_history_page_has_no_timeline_before_signings_close(signed_in, roster, season):
    season.signings_close_at = timezone.now() + dt.timedelta(days=3)
    season.save()
    squad(roster)

    response = signed_in.get(history_url(roster))

    assert response.context["timeline"] is None


def test_someone_elses_timeline_stays_hidden(signed_in, season, other_manager):
    theirs = Roster.objects.create(manager=other_manager, season=season, name="Theirs")
    squad(theirs)

    assert signed_in.get(history_url(theirs)).status_code == 404


def test_the_timeline_speaks_german_to_german_users(signed_in, roster, user):
    user.language = "de"
    user.save(update_fields=["language"])
    players = squad(roster)
    trade(roster, players[0], player("G", "Newcomer"), at(11, 3))

    response = signed_in.get(history_url(roster))
    body = response.content.decode()

    assert "Kader-Zeitleiste" in body
    assert "Der Startkader und 1 Trade seither." in body
    # The strings the script fills in arrive translated, placeholders intact.
    assert "Am {date} für {partner} geholt" in body
    assert response.context["timeline"]["spots"][0]["stints"][0]["came"] is None
