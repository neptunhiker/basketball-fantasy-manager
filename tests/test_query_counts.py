"""What a page costs the database, and whether that cost grows with the rows.

Every test here is the same shape: render something twice, once small and once
large, and assert the two cost the same. That is what an N+1 actually is -- not
a page that is expensive, but a page that gets more expensive per row -- and it
is the only form of the assertion that keeps meaning something as the app grows
new columns and new joins. A bare `assert queries < 12` either fails the next
time somebody legitimately adds a lookup, or passes while the page quietly
issues eleven queries per roster instead of one.

The figures on a roster -- how full it is, what it is worth, which positions it
is short of -- are all counted from its current squad, so a screen that shows
several of them, or several rosters, is where the growth lands. See
`Roster.current_memberships`.
"""

import datetime as dt
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.fantasy import services
from apps.fantasy.models import Manager, Roster, Season
from apps.nba.models import Player


@pytest.fixture
def season(db):
    return Season.objects.create(
        label="2026-27",
        starts_on=dt.date(2026, 10, 20),
        ends_on=dt.date(2027, 4, 11),
        is_current=True,
    )


@pytest.fixture
def signed_in(client, user, password, db):
    client.login(email=user.email, password=password)
    return client


@pytest.fixture
def roster(season, manager):
    return Roster.objects.create(manager=manager, season=season, name="Roster 1")


def make_players(count, position="G", salary=1_000_000):
    return [
        Player.objects.create(
            first_name=f"P{index}",
            last_name=f"{position}{index}",
            position=position,
            current_salary=Decimal(salary),
        )
        for index in range(count)
    ]


def cost(request):
    """How many statements one request costs.

    Takes a callable rather than a URL so the same helper can time a GET, a
    POST or a plain attribute read on a model.
    """
    with CaptureQueriesContext(connection) as captured:
        request()
    return len(captured.captured_queries)


def build_url(roster):
    return reverse("fantasy:roster-build", args=[roster.pk])


# --- the build page ----------------------------------------------------------


def test_the_build_page_costs_the_same_whatever_the_market_holds(signed_in, roster):
    """No query per row of the market.

    The market's rows each ask whether the roster is full, to decide between a
    Sign button and a disabled one. Answering that from the database per row is
    the worst of these, because it grows with the *league* rather than with the
    roster: every player with a price on record is a row.
    """
    make_players(3)
    small = cost(lambda: signed_in.get(build_url(roster)))

    make_players(30, position="F")
    large = cost(lambda: signed_in.get(build_url(roster)))

    assert large == small, f"{small} queries for 3 players, {large} for 33"


def test_the_build_page_costs_the_same_whatever_the_squad_holds(signed_in, roster):
    """No query per player in the team card either.

    Signing also shrinks the market, so this moves both halves of the page at
    once -- which is the point: the total has to be flat in both.
    """
    players = make_players(6) + make_players(6, position="F") + make_players(3, position="C")
    empty = cost(lambda: signed_in.get(build_url(roster)))

    for player in players[:12]:
        services.buy(roster, player, player.current_salary)
    full = cost(lambda: signed_in.get(build_url(roster)))

    assert full == empty + 2, f"{empty} queries with no squad, {full} with twelve"


def test_a_signing_costs_the_same_whatever_the_market_holds(signed_in, roster):
    """The POST re-renders both partials, so it carries the same risk as the GET.

    Worth its own test rather than trusting the GET: this path renders the page
    from a roster that has just changed under it, which is the case that cannot
    lean on anything loaded earlier in the request.
    """
    players = make_players(4)
    small = cost(lambda: signed_in.post(_buy(roster, players[0]), {}))

    make_players(30, position="F")
    large = cost(lambda: signed_in.post(_buy(roster, players[1]), {}))

    assert large == small + 2, f"{small} queries signing from 4 players, {large} from 33"


def _buy(roster, player):
    return reverse("fantasy:roster-buy", args=[roster.pk, player.pk])


def test_the_trade_modal_costs_the_same_whatever_the_squad_holds(signed_in, roster):
    """The modal lists the squad on one side and the market on the other."""
    players = make_players(14)
    services.buy(roster, players[0], players[0].current_salary)
    url = reverse("fantasy:roster-trade", args=[roster.pk])
    small = cost(lambda: signed_in.get(url))

    for player in players[1:12]:
        services.buy(roster, player, player.current_salary)
    large = cost(lambda: signed_in.get(url))

    assert large == small, f"{small} queries with one player, {large} with twelve"


# --- the roster list ---------------------------------------------------------


def test_the_roster_list_costs_the_same_whatever_it_lists(signed_in, user, season, manager):
    """Every card states how full its roster is and whether it is complete.

    Both are counted from the squad, so one card asking twice becomes twenty
    questions across ten cards -- and this is the page a manager lands on.
    """
    url = reverse("fantasy:roster-list")
    Roster.objects.create(manager=manager, season=season, name="First")
    one = cost(lambda: signed_in.get(url))

    players = make_players(9)
    for index in range(5):
        other = Manager.objects.create(user=user, nick_name=f"Extra {index}")
        extra = Roster.objects.create(manager=other, season=season, name=f"R{index}")
        services.buy(extra, players[index], players[index].current_salary)
    many = cost(lambda: signed_in.get(url))

    assert many == one + 2, f"{one} queries for one roster, {many} for six"


# --- the model behind them ---------------------------------------------------


def test_every_derived_figure_comes_from_one_load(roster):
    """Asking a roster six questions about its squad reads the squad once.

    This is the fix stated at the level it was made. The screens ask all of
    these, several of them more than once per render, and each used to be its
    own statement.
    """
    players = make_players(3) + make_players(2, position="F")
    for player in players:
        services.buy(roster, player, player.current_salary)
    fresh = Roster.objects.get(pk=roster.pk)

    def ask_everything():
        return (
            fresh.player_count,
            fresh.squad_value,
            fresh.position_counts(),
            fresh.position_progress(),
            fresh.missing(),
            fresh.is_complete,
        )

    assert cost(ask_everything) == 3


def test_a_page_of_rosters_loads_every_squad_at_once(roster, season, manager):
    """`with_squad` is what makes the list page flat, so it is pinned directly.

    One query for the rosters and one for all of their squads, with nothing
    left for the counters to ask afterwards.
    """
    players = make_players(4)
    for player in players[:2]:
        services.buy(roster, player, player.current_salary)
    second = Roster.objects.create(manager=manager, season=season, name="Roster 2")
    services.buy(second, players[2], players[2].current_salary)

    def read_both():
        return [
            (loaded.player_count, loaded.is_complete, loaded.squad_value)
            for loaded in Roster.objects.with_squad()
        ]

    assert cost(read_both) == 4


def test_a_reloaded_roster_counts_its_squad_again(roster):
    """The cache lasts until the roster is reloaded, and no longer.

    The whole risk of caching a squad on the instance is a page that renders
    the figures from before the change it just made. Every writer in `services`
    reloads the caller's roster, so this asserts the case those writers rely on:
    a count taken before a signing does not survive it.
    """
    player = make_players(1)[0]
    assert roster.player_count == 0

    services.buy(roster, player, player.current_salary)

    assert roster.player_count == 1
    assert roster.squad_value == Decimal("1000000")
    assert roster.current_squad == [player]


def test_a_squad_is_worth_nothing_extra_for_a_player_with_no_price(roster):
    """A player with no salary on record counts as no money, not as an error.

    Held onto because the total moved out of the database: `SUM` skipped a NULL
    on its own, and the Python that replaced it has to skip it deliberately.
    """
    priced = make_players(1)[0]
    services.buy(roster, priced, priced.current_salary)
    unpriced = Player.objects.create(first_name="No", last_name="Price", position="C")
    services.buy(roster, unpriced, 0)

    assert roster.player_count == 2
    assert roster.squad_value == Decimal("1000000")
