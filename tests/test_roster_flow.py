import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.fantasy import services
from apps.fantasy.models import MINIMUM_BY_POSITION, ROSTER_SIZE, Manager, Roster, Season
from apps.nba.models import Player

User = get_user_model()


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


def make_player(position, name, salary):
    player = Player.objects.create(
        first_name=name, last_name=position, position=position, current_salary=Decimal(salary)
    )
    return player


@pytest.fixture
def pool(db):
    """Cheap enough that a full roster fits inside the cap several times over."""
    return {
        position: [make_player(position, f"P{i}", 1_000_000) for i in range(8)]
        for position in Player.Position.values
    }


@pytest.fixture
def roster(season, manager):
    return Roster.objects.create(manager=manager, season=season, name="Roster 1")


def build_url(roster):
    return reverse("fantasy:roster-build", args=[roster.pk])


def buy_url(roster, player):
    return reverse("fantasy:roster-buy", args=[roster.pk, player.pk])


def sell_url(roster, player):
    return reverse("fantasy:roster-sell", args=[roster.pk, player.pk])


# --- creation ----------------------------------------------------------------


def test_creating_a_roster_uses_the_name_the_user_gave(signed_in, season, manager):
    response = signed_in.post(reverse("fantasy:roster-create"), {"name": "Bulla Ballers"})
    roster = Roster.objects.get()

    assert response.status_code == 302
    assert response.url == reverse("fantasy:roster-rules", args=[roster.pk])
    assert roster.season == season
    assert roster.cash == Decimal("60000000")
    assert roster.name == "Bulla Ballers"


def test_creating_without_a_current_season_explains_itself(signed_in, db):
    response = signed_in.post(reverse("fantasy:roster-create"), {"name": "Egal"})
    assert response.status_code == 409
    assert "No season is marked as current" in response.content.decode()
    assert not Roster.objects.exists()


# --- ownership ---------------------------------------------------------------


def test_another_users_roster_is_not_found(signed_in, roster, season, other_manager):
    theirs = Roster.objects.create(manager=other_manager, season=season, name="Theirs")

    for url in [build_url(theirs), reverse("fantasy:roster-rules", args=[theirs.pk])]:
        assert signed_in.get(url).status_code == 404


def test_cannot_buy_into_another_users_roster(signed_in, season, pool, other_manager):
    theirs = Roster.objects.create(manager=other_manager, season=season, name="Theirs")

    response = signed_in.post(buy_url(theirs, pool["G"][0]))
    assert response.status_code == 404
    assert theirs.player_count == 0


# --- the rules screen --------------------------------------------------------


def test_rules_screen_shows_the_real_constants(signed_in, roster):
    response = signed_in.get(reverse("fantasy:roster-rules", args=[roster.pk]))
    assert response.status_code == 200
    assert response.context["roster_size"] == ROSTER_SIZE
    assert response.context["required_slots"] == sum(MINIMUM_BY_POSITION.values())
    assert response.context["flex_slots"] == ROSTER_SIZE - sum(MINIMUM_BY_POSITION.values())

    body = response.content.decode()
    assert str(ROSTER_SIZE) in body
    # Shown in millions, with the exact figure on hover.
    assert "$60.00M" in body
    assert 'title="$60,000,000"' in body


# --- buying ------------------------------------------------------------------


def test_buying_updates_cash_counters_and_squad(signed_in, roster, pool):
    player = pool["G"][0]
    response = signed_in.post(buy_url(roster, player), headers={"HX-Request": "true"})
    body = response.content.decode()

    roster.refresh_from_db()
    assert roster.cash == Decimal("59000000")
    assert player in roster.current_players
    assert player.full_name in body
    assert "1 / 15" in body


def test_the_response_carries_both_panels(signed_in, roster, pool):
    """The picker rides along out of band, so affordability is re-marked."""
    body = signed_in.post(
        buy_url(roster, pool["G"][0]), headers={"HX-Request": "true"}
    ).content.decode()
    assert 'id="roster-panel"' in body
    assert 'hx-swap-oob="outerHTML"' in body
    assert 'id="player-picker"' in body


def test_the_price_comes_from_the_server_not_the_request(signed_in, roster, pool):
    player = pool["G"][0]
    signed_in.post(buy_url(roster, player), {"price": "1", "current_salary": "1"})
    roster.refresh_from_db()
    assert roster.cash == Decimal("59000000")


def test_a_player_without_a_salary_cannot_be_signed(signed_in, roster, db):
    unpriced = Player.objects.create(first_name="No", last_name="Price", position="G")
    body = signed_in.post(buy_url(roster, unpriced)).content.decode()

    roster.refresh_from_db()
    assert roster.player_count == 0
    assert "No players with a salary" in body


def test_an_unaffordable_player_is_refused_with_a_reason(signed_in, roster, db):
    expensive = make_player("C", "Too", 61_000_000)
    body = signed_in.post(buy_url(roster, expensive)).content.decode()

    roster.refresh_from_db()
    assert roster.cash == Decimal("60000000")
    assert "Not enough cash" in body


def test_unaffordable_players_are_marked_in_the_picker(signed_in, roster, db):
    make_player("C", "Too", 61_000_000)
    body = signed_in.get(build_url(roster)).content.decode()
    assert "Too dear" in body


def test_a_full_roster_stops_accepting_players(signed_in, roster, pool):
    for position, count in [("G", 5), ("F", 5), ("C", 5)]:
        for player in pool[position][:count]:
            services.buy(roster, player, player.current_salary)
    roster.refresh_from_db()
    assert roster.player_count == ROSTER_SIZE

    body = signed_in.post(buy_url(roster, pool["G"][6])).content.decode()
    roster.refresh_from_db()
    assert roster.player_count == ROSTER_SIZE
    assert "full" in body.lower()


def test_a_player_already_signed_leaves_the_picker(signed_in, roster, pool):
    player = pool["G"][0]
    assert player.full_name in signed_in.get(build_url(roster)).content.decode()

    services.buy(roster, player, player.current_salary)
    picker = signed_in.get(build_url(roster), headers={"HX-Request": "true"}).content.decode()
    assert player.full_name not in picker


# --- removing ----------------------------------------------------------------


def test_removing_refunds_exactly_what_was_paid(signed_in, roster, pool):
    player = pool["F"][0]
    signed_in.post(buy_url(roster, player))
    # A weekly recalculation moves the market price after the purchase.
    player.current_salary = Decimal("4000000")
    player.save(update_fields=["current_salary"])

    signed_in.post(sell_url(roster, player))
    roster.refresh_from_db()
    assert roster.cash == Decimal("60000000")
    assert player not in roster.current_players


# --- completeness ------------------------------------------------------------


def test_the_panel_reports_completeness(signed_in, roster, pool):
    for position, count in [("G", 6), ("F", 6), ("C", 3)]:
        for player in pool[position][:count]:
            services.buy(roster, player, player.current_salary)

    body = signed_in.get(build_url(roster)).content.decode()
    assert "Every minimum met" in body


def test_the_panel_names_what_is_still_missing(signed_in, roster, pool):
    services.buy(roster, pool["G"][0], 1_000_000)
    body = signed_in.get(build_url(roster)).content.decode()
    assert "4 more Guards" in body
    assert "5 more Forwards" in body


# --- filters and renaming ----------------------------------------------------


def test_filters_survive_a_purchase(signed_in, roster, pool):
    body = signed_in.post(
        buy_url(roster, pool["G"][0]),
        {"q": "P1", "position": "G", "team": ""},
        headers={"HX-Request": "true"},
    ).content.decode()
    assert 'value="P1"' in body
    assert '<option value="G" selected' in body


def test_renaming_a_roster(signed_in, roster):
    signed_in.post(reverse("fantasy:roster-rename", args=[roster.pk]), {"name": "Bulla FC"})
    roster.refresh_from_db()
    assert roster.name == "Bulla FC"


def test_renaming_to_blank_is_ignored(signed_in, roster):
    signed_in.post(reverse("fantasy:roster-rename", args=[roster.pk]), {"name": "   "})
    roster.refresh_from_db()
    assert roster.name == "Roster 1"


# --- the shape of the build page ---------------------------------------------


def test_the_team_comes_before_the_market(signed_in, roster, pool):
    """The order is the layout, and the layout is the point of this screen.

    Asserted as document order rather than by looking for classes: which
    utilities produce the stack is free to change, but the team card appearing
    above the player list is what was asked for.
    """
    body = signed_in.get(reverse("fantasy:roster-build", args=[roster.pk])).content.decode()

    assert body.index('id="roster-panel"') < body.index('id="player-picker"')


def test_the_team_card_carries_the_figures_the_page_is_read_for(signed_in, roster, pool):
    body = signed_in.get(reverse("fantasy:roster-build", args=[roster.pk])).content.decode()
    card = body[body.index('id="roster-panel"') : body.index('id="player-picker"')]

    for label in ("Cash", "Roster value", "Players", "Your roster"):
        assert label in card, label


def test_the_summary_shows_players_by_position(signed_in, roster, pool):
    services.buy(roster, pool["G"][0], pool["G"][0].current_salary)
    services.buy(roster, pool["F"][0], pool["F"][0].current_salary)

    response = signed_in.get(build_url(roster))
    counts = {position["code"]: position["count"] for position in response.context["progress"]}

    assert counts == {"G": 1, "F": 1, "C": 0}
    assert 'aria-label="Players by position"' in response.content.decode()


def squad_of(body):
    """Just the grouped roster from the team card.

    Sliced from the "Your roster" heading rather than from the top of the card,
    because the summary strip above it names positions too -- "Still needed:
    4 more Guards" -- and a search for "Guards" would find that sentence first.
    """
    card = body[body.index('id="roster-panel"') : body.index('id="player-picker"')]
    return card[card.index("Your roster") :]


def test_the_roster_uses_a_players_style_table_with_trade_actions(signed_in, roster, pool):
    player = pool["G"][0]
    services.buy(roster, player, player.current_salary)

    body = signed_in.get(build_url(roster)).content.decode()
    squad = squad_of(body)

    assert "<table" in squad
    assert all(
        label in squad
        for label in (
            "Player",
            "Position",
            "Team",
            "Actual salary",
            "Expected salary",
            "Difference",
            "Points",
            "Avg/game",
            "Games",
            "Hotness",
            "Injury",
            "Status",
            "Actions",
        )
    )
    assert player.full_name in squad
    assert f'aria-label="Trade {player.full_name}"' in squad
    assert f'hx-get="{reverse("fantasy:roster-trade", args=[roster.pk])}?out={player.pk}"' in squad


def test_roster_columns_sort_rows_and_toggle_direction(signed_in, roster, pool):
    low, high = pool["G"][:2]
    Player.objects.filter(pk=low.pk).update(current_total_fp=10)
    Player.objects.filter(pk=high.pk).update(current_total_fp=20)
    for player in (low, high):
        services.buy(roster, player, player.current_salary)

    body = signed_in.get(build_url(roster), {"sort": "points", "dir": "desc"}).content.decode()
    squad = squad_of(body)

    assert squad.index(high.full_name) < squad.index(low.full_name)
    assert "sort=points&amp;dir=asc" in squad


def test_the_roster_table_keeps_release_action_when_signings_are_open(signed_in, roster, pool):
    player = pool["C"][0]
    services.buy(roster, player, player.current_salary)

    body = signed_in.get(build_url(roster)).content.decode()

    assert f'aria-label="Release {player.full_name}"' in body


def test_the_roster_table_keeps_unknown_positions(signed_in, roster):
    stray = make_player("X", "Stray", 1_000_000)
    services.buy(roster, stray, stray.current_salary)

    body = signed_in.get(build_url(roster)).content.decode()

    assert stray.full_name in squad_of(body)


# --- the strip that stays in view --------------------------------------------


def strip_of(body):
    """The pinned summary above the market, without the market itself.

    Sliced from the picker's id to the card that follows it, which is also the
    structural claim being made: the strip has to sit *outside* that card,
    because the card is `overflow-hidden` and a sticky element inside one is
    clipped to it instead of pinning to the viewport.
    """
    picker = body[body.index('id="player-picker"') :]
    return picker[: picker.index('class="card overflow-hidden"')]


def test_the_market_keeps_the_budget_in_view(signed_in, roster, pool):
    """Cash scrolls away with the team card; the market is where it is needed.

    The figures are asserted inside the strip's own slice rather than anywhere
    on the page -- the team card states all of them too, so `in body` would
    pass with no strip at all.
    """
    for player in pool["G"][:2]:
        services.buy(roster, player, player.current_salary)

    strip = " ".join(strip_of(signed_in.get(build_url(roster)).content.decode()).split())

    assert "sticky" in strip, "the strip is not pinned"
    assert "Cash" not in strip
    assert f"2 / {ROSTER_SIZE}" not in strip


def test_the_team_panel_names_missing_positions(signed_in, roster, pool):
    """The summary still explains roster gaps above the player table."""
    for player in pool["G"][:2]:
        services.buy(roster, player, player.current_salary)

    body = signed_in.get(build_url(roster)).content.decode()

    assert "Still needed" in body
    assert "3 more Guards" in body
    assert "5 more Forwards" in body


def test_a_signing_brings_the_strip_with_it(signed_in, roster, pool):
    """The whole reason the strip lives under the picker's id.

    A buy swaps the team card and sends the picker back out of band. Because
    the strip is inside that fragment, its cash is re-rendered by the response
    that already existed -- no second swap target to keep in step. If it ever
    stops arriving, the pinned figure goes stale while the card above it is
    correct, which is worse than not showing it.
    """
    player = pool["G"][0]
    body = signed_in.post(buy_url(roster, player), {}, HTTP_HX_REQUEST="true").content.decode()

    roster.refresh_from_db()
    strip = " ".join(strip_of(body).split())

    assert "hx-swap-oob" in strip, "the picker fragment is not marked out of band"
    assert f"1 / {ROSTER_SIZE}" not in strip
    assert "$59.00M" not in strip


def test_filtering_the_market_does_not_drop_the_strip(signed_in, roster, pool):
    """A search returns the picker alone, and the strip rides inside it.

    Sliced the same way, so a strip that had been left in the card would fail
    here as well as on the full page.
    """
    body = signed_in.get(build_url(roster), {"q": "P0"}, HTTP_HX_REQUEST="true").content.decode()

    strip = " ".join(strip_of(body).split())

    assert "Cash" not in strip
    assert f"0 / {ROSTER_SIZE}" not in strip


# --- reaching the player behind the name -------------------------------------


def market_of(body):
    """The market list, without the team card above it.

    The two lists print the same names -- a squad member is only a market row
    that was signed -- so a test that wants to say *which* list carries a link
    has to slice first or it proves nothing.
    """
    return body[body.index('id="player-picker"') :]


def detail_url(player):
    return reverse("nba:player-detail", args=[player.slug])


def test_a_squad_players_name_links_to_them(signed_in, roster, pool):
    """The name in the team card is the way through to the player."""
    player = pool["G"][0]
    signed_in.post(buy_url(roster, player), {})

    squad = squad_of(signed_in.get(build_url(roster)).content.decode())

    assert f'href="{detail_url(player)}"' in squad


def test_a_market_players_name_links_to_them(signed_in, roster, pool):
    """Reachable before signing, which is when the detail page is wanted.

    Deciding whether a salary is worth paying is exactly the moment the reader
    needs the player's numbers, so the link cannot wait until they are signed.
    """
    market = market_of(signed_in.get(build_url(roster)).content.decode())

    assert f'href="{detail_url(pool["G"][0])}"' in market


def test_every_player_on_the_page_is_reachable(signed_in, roster, pool):
    """No name on this page is a dead end -- squad or market, either list.

    Asserted over the whole pool rather than one player from each list: the
    two lists render from different templates, and a link added to one of them
    only would pass a narrower check while leaving half the names inert.
    """
    for position in ("G", "F", "C"):
        signed_in.post(buy_url(roster, pool[position][0]), {})

    body = signed_in.get(build_url(roster)).content.decode()

    unreachable = [
        player.full_name
        for players in pool.values()
        for player in players
        if f'href="{detail_url(player)}"' not in body
    ]
    assert unreachable == []


def test_the_links_do_not_cost_a_query_per_player(signed_in, roster, pool):
    """The slug comes with the row it is printed on.

    `{% url %}` on `player.slug` is free only while the field is loaded. If a
    view ever narrows its queryset with `only()` or `defer()` and leaves the
    slug out, every name on the page fetches it again and the market's two
    dozen rows become two dozen queries -- silently, since the page still
    renders correctly.
    """
    for position in ("G", "F", "C"):
        signed_in.post(buy_url(roster, pool[position][0]), {})

    with CaptureQueriesContext(connection) as captured:
        signed_in.get(build_url(roster))

    # Counted on the slug column specifically, not on `nba_player`: the page
    # already runs one `position`-only query per market row, because the loop
    # tests `roster.is_complete` and that recounts the squad each time. That is
    # a separate, older problem, and a broad query count here would fail for it
    # rather than for anything these links do.
    fetched = [q["sql"] for q in captured.captured_queries if '"nba_player"."slug"' in q["sql"]]
    # The list queries that render the two lists, not one per name.
    assert len(fetched) <= 5, f"{len(fetched)} queries fetched player slugs"


# --- whose roster it is ------------------------------------------------------
#
# The handle sits in the header bar next to the roster name, with a copy in the
# content block for widths where the bar has no room for it. The two are
# exclusive by breakpoint -- `md:flex` against `md:hidden` -- so these tests
# check each one where it lives rather than searching the whole page, which
# would pass on either half alone.


def header_of(body):
    """The top bar: everything before `</header>`, where the name lives."""
    return body.split("</header>")[0]


def test_the_header_names_the_managing_profile(signed_in, roster):
    """The point of the line: the handle the roster is played under.

    Not inferable from being signed in -- an account can hold several profiles
    and a roster belongs to exactly one of them.
    """
    header = header_of(signed_in.get(build_url(roster)).content.decode())

    assert "Managed by" in header
    assert "Bulla" in header


def test_the_handle_shares_the_bar_with_the_name(signed_in, roster):
    """One line, which is the whole reason it moved up here.

    Both in the header, so nothing was spent on a second row -- and the roster
    name is still the editable field it was.
    """
    header = header_of(signed_in.get(build_url(roster)).content.decode())

    assert 'id="roster-name-input"' in header
    assert "Managed by" in header


def test_the_managing_profile_is_the_rosters_own(signed_in, season, user, manager):
    """Two profiles on one account, so the page has to pick the right one.

    `manager` is pulled in for "Bulla" to exist and be the wrong answer -- with
    one profile on the account, a page rendering whichever handle it found
    first would pass anyway.
    """
    second = Manager.objects.create(user=user, nick_name="Buzz")
    roster = Roster.objects.create(manager=second, season=season, name="Second squad")

    body = signed_in.get(build_url(roster)).content.decode()

    assert "Buzz" in body
    assert "Bulla" not in body


def test_the_nickname_links_to_the_managers_page(signed_in, roster):
    """Where the handle can be renamed, and the only place the others are listed."""
    header = header_of(signed_in.get(build_url(roster)).content.decode())
    managers = reverse("fantasy:manager-list")

    chip = header.split("Managed by")[1].split("</span>")[0]
    assert f'href="{managers}"' in chip


def test_a_narrow_screen_keeps_the_handle_below_the_bar(signed_in, roster):
    """The other half of the pair, for widths where the bar is already full.

    A breakpoint is the behaviour here, so the classes are what there is to
    assert: the content copy hides at `md`, which is exactly where the
    header's appears. Neither width shows it twice, and none shows it never.
    """
    body = signed_in.get(build_url(roster)).content.decode()
    content = body.split("</header>")[1]

    below = content.split("Managed by")[1].split("</p>")[0]
    assert f'href="{reverse("fantasy:manager-list")}"' in below
    # The two halves, and the classes that keep them from overlapping.
    assert body.count("Managed by") == 2
    assert "md:hidden" in content.split("Managed by")[0].rsplit("<p ", 1)[1]
    assert "md:flex" in header_of(body).split("Managed by")[0].rsplit("<span ", 1)[1]


def test_the_profile_arrives_with_the_roster(signed_in, roster):
    """`OwnRosterMixin` select_relates the manager, and this line relies on it.

    A regression would be silent -- the page would still say the right thing,
    just one query dearer -- so it is asserted rather than trusted. Read off
    the SQL rather than a total count, which any unrelated change would move:
    the manager joined to the roster is one statement naming both tables, and
    a lazy fetch is a second statement naming only `fantasy_manager`.
    """
    with CaptureQueriesContext(connection) as captured:
        signed_in.get(build_url(roster))

    lazy = [
        query["sql"]
        for query in captured.captured_queries
        if "fantasy_manager" in query["sql"] and "fantasy_roster" not in query["sql"]
    ]
    assert lazy == []
