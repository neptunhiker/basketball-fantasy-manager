"""Round 2 of the UX fixes: guidance while building a roster.

2b deadline line, 2c toasts, 2d next free trade + Rules on phones,
2e trade dialog badges and visible reasons, 2f from a player into a roster.
"""

import datetime as dt
import json
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.fantasy import services
from apps.fantasy.models import Roster, Season
from apps.nba.models import Player


def toast(response):
    return json.loads(response["HX-Trigger-After-Swap"])["toast"]


def make_player(last_name, position="G", salary=1_000_000):
    return Player.objects.create(
        first_name="P", last_name=last_name, position=position, current_salary=salary
    )


@pytest.fixture
def signed_in(client, user, password):
    client.login(email=user.email, password=password)
    return client


def make_season(*, signings_close_in=None, signings_open_in=None, started_days_ago=30):
    """A current season; signing window relative to now (None = no window)."""
    now = timezone.now()
    today = timezone.localdate()
    return Season.objects.create(
        label="Live",
        starts_on=today - dt.timedelta(days=started_days_ago),
        ends_on=today + dt.timedelta(days=150),
        is_current=True,
        signings_open_at=now + signings_open_in if signings_open_in is not None else None,
        signings_close_at=now + signings_close_in if signings_close_in is not None else None,
    )


@pytest.fixture
def signing_season(db):
    """Signings open (opened a day ago, close in three days)."""
    return make_season(
        signings_open_in=-dt.timedelta(days=1),
        signings_close_in=dt.timedelta(days=3),
        started_days_ago=-10,
    )


@pytest.fixture
def trading_season(db):
    """Signings closed two days ago; the season is running."""
    return make_season(
        signings_open_in=-dt.timedelta(days=20), signings_close_in=-dt.timedelta(days=2)
    )


@pytest.fixture
def open_season(db):
    """No signing window at all: buying, selling and trading always allowed."""
    return make_season()


def build_page(client, roster):
    return client.get(reverse("fantasy:roster-build", args=[roster.pk])).text


# --- 2c: toasts --------------------------------------------------------------------------


def test_signing_says_so_in_a_toast(signed_in, manager, open_season):
    roster = Roster.objects.create(manager=manager, season=open_season, name="R")
    player = make_player("Adebayo", "C", 10_170_000)

    response = signed_in.post(reverse("fantasy:roster-buy", args=[roster.pk, player.pk]))

    assert toast(response) == {"message": "P Adebayo signed for $10.17M.", "level": "success"}


def test_a_refused_signing_is_an_error_toast_not_a_box_off_screen(signed_in, manager, open_season):
    roster = Roster.objects.create(manager=manager, season=open_season, name="R")
    player = make_player("Pricey", "C", 70_000_000)

    response = signed_in.post(reverse("fantasy:roster-buy", args=[roster.pk, player.pk]))

    assert toast(response)["level"] == "error"
    assert "Not enough cash" in toast(response)["message"]
    assert 'role="alert"' not in response.text


def test_releasing_says_what_came_back(signed_in, manager, open_season):
    roster = Roster.objects.create(manager=manager, season=open_season, name="R")
    player = make_player("Kept", "G", 2_000_000)
    services.buy(roster, player, Decimal("2000000"))

    response = signed_in.post(reverse("fantasy:roster-sell", args=[roster.pk, player.pk]))

    assert toast(response)["message"] == "P Kept released, $2.00M back."


def test_buying_a_trade_and_trading_are_confirmed(signed_in, manager, open_season):
    roster = Roster.objects.create(manager=manager, season=open_season, name="R")
    out_player, in_player = make_player("Out"), make_player("In")
    services.buy(roster, out_player, Decimal("1000000"))

    bought = signed_in.post(
        reverse("fantasy:roster-buy-trade", args=[roster.pk]), headers={"HX-Request": "true"}
    )
    traded = signed_in.post(
        reverse("fantasy:roster-trade", args=[roster.pk])
        + f"?out={out_player.pk}&in={in_player.pk}"
    )

    assert toast(bought)["message"] == "Trade bought for $1.50M."
    assert toast(traded)["message"] == "Trade done: P Out out, P In in."


def test_the_page_has_one_toast_area(signed_in, manager, open_season):
    roster = Roster.objects.create(manager=manager, season=open_season, name="R")

    assert build_page(signed_in, roster).count("@toast.window") == 1


# --- 2b: signings deadline -----------------------------------------------------------------


def test_the_build_page_says_when_signings_close(signed_in, manager, signing_season):
    roster = Roster.objects.create(manager=manager, season=signing_season, name="R")

    body = build_page(signed_in, roster)

    assert "Signings close in 2" in body or "Signings close in 3" in body
    assert "bg-amber-50 font-medium" not in body


def test_the_last_day_is_amber(signed_in, manager, db):
    season = make_season(
        signings_open_in=-dt.timedelta(days=1),
        signings_close_in=dt.timedelta(hours=5),
        started_days_ago=-10,
    )
    roster = Roster.objects.create(manager=manager, season=season, name="R")

    body = build_page(signed_in, roster)

    assert "Signings close in" in body
    assert "bg-amber-50 font-medium" in body


def test_before_signings_open_it_says_when_they_open(signed_in, manager, db):
    season = make_season(
        signings_open_in=dt.timedelta(days=2),
        signings_close_in=dt.timedelta(days=9),
        started_days_ago=-20,
    )
    manager_roster = Roster.objects.create(manager=manager, season=season, name="R")

    assert "Signings open in" in build_page(signed_in, manager_roster)


def test_after_the_deadline_there_is_no_countdown(signed_in, manager, trading_season):
    roster = Roster.objects.create(manager=manager, season=trading_season, name="R")

    assert "Signings close in" not in build_page(signed_in, roster)


# --- 2d: next free trade and Rules ------------------------------------------------------------


def test_the_trades_box_names_the_next_free_trade(signed_in, manager, open_season):
    open_season.trade_grants.create(
        label="Helpside Trade", granted_at=timezone.now() + dt.timedelta(days=4), trades=1
    )
    roster = Roster.objects.create(manager=manager, season=open_season, name="R")

    body = build_page(signed_in, roster)

    assert "Next: Helpside Trade" in body
    assert reverse("fantasy:roster-rules", args=[roster.pk]) + "#trades" in body


def test_the_rules_link_is_there_at_every_width(signed_in, manager, open_season):
    roster = Roster.objects.create(manager=manager, season=open_season, name="R")
    header = build_page(signed_in, roster).split('id="roster-panel"')[0]

    rules_link = header[header.index(reverse("fantasy:roster-rules", args=[roster.pk])) :]
    rules_link = rules_link[: rules_link.index("</a>")]
    assert 'aria-label="Rules"' in rules_link
    assert "hidden text-xs sm:inline-flex" not in rules_link


def test_the_calendar_marks_received_and_next_grants(signed_in, manager, open_season):
    now = timezone.now()
    open_season.trade_grants.create(label="Old one", granted_at=now - dt.timedelta(days=3))
    open_season.trade_grants.create(label="Coming up", granted_at=now + dt.timedelta(days=3))
    open_season.trade_grants.create(label="Much later", granted_at=now + dt.timedelta(days=30))
    roster = Roster.objects.create(manager=manager, season=open_season, name="R")

    body = signed_in.get(reverse("fantasy:roster-rules", args=[roster.pk])).text

    assert 'id="trades"' in body
    assert body.count(">Received<") == 1
    assert body.count(">Next<") == 1
    assert body.index("Coming up") < body.index(">Next<") < body.index("Much later")


# --- 2e: the trade dialog --------------------------------------------------------------------


@pytest.fixture
def full_roster(manager, open_season):
    """Exactly the minimums plus three forwards: 5 G, 8 F, 2 C."""
    roster = Roster.objects.create(
        manager=manager, season=open_season, name="R", trades_available=1
    )
    Roster.objects.filter(pk=roster.pk).update(cash=Decimal("100000000"))
    for n, position in enumerate("GGGGG" + "FFFFFFFF" + "CC"):
        services.buy(roster, make_player(f"Own{n}", position), Decimal("1000000"))
    return roster


def trade_dialog(client, roster, **params):
    query = "&".join(f"{key}={value}" for key, value in params.items())
    return client.get(reverse("fantasy:roster-trade", args=[roster.pk]) + f"?body=1&{query}").text


def test_a_swap_that_leaves_a_position_short_gets_a_badge(signed_in, full_roster):
    guard = next(p for p in full_roster.current_squad if p.position == "G")
    make_player("NewCenter", "C")
    make_player("NewGuard", "G")

    body = trade_dialog(signed_in, full_roster, out=guard.pk)

    center_row = body[body.index("P NewCenter") :][:600]
    guard_row = body[body.index("P NewGuard") :][:600]
    assert "leaves 4 G" in center_row
    assert "leaves" not in guard_row


def test_a_swap_from_a_spare_position_gets_no_badge(signed_in, full_roster):
    forward = next(p for p in full_roster.current_squad if p.position == "F")
    make_player("NewCenter", "C")

    body = trade_dialog(signed_in, full_roster, out=forward.pk)

    assert "leaves" not in body[body.index("P NewCenter") :][:600]


def test_no_badges_before_the_outgoing_player_is_chosen(signed_in, full_roster):
    make_player("NewCenter", "C")

    assert "leaves " not in trade_dialog(signed_in, full_roster)


def test_why_execute_is_disabled_is_written_out(signed_in, full_roster):
    body = trade_dialog(signed_in, full_roster)

    assert 'id="trade-blocked-reason"' in body
    assert "Pick a player to send out and one to bring in." in body


# --- 2f: from a player into a roster -----------------------------------------------------------


def player_page(client, player):
    return client.get(reverse("nba:player-detail", args=[player.slug])).text


def test_while_signings_are_open_the_player_page_offers_signing(signed_in, manager, signing_season):
    roster = Roster.objects.create(manager=manager, season=signing_season, name="Bulla")
    target = make_player("Target", "G", 3_000_000)

    body = player_page(signed_in, target)

    assert "Your rosters" in body
    assert "Bulla" in body
    assert reverse("fantasy:roster-sign-player", args=[roster.pk, target.pk]) in body
    assert "Sign ($3.00M)" in body


def test_signing_from_the_player_page_uses_the_server_price(signed_in, manager, signing_season):
    roster = Roster.objects.create(manager=manager, season=signing_season, name="Bulla")
    target = make_player("Target", "G", 3_000_000)

    response = signed_in.post(
        reverse("fantasy:roster-sign-player", args=[roster.pk, target.pk]), {"price": "1"}
    )

    roster.refresh_from_db()
    assert roster.cash == roster.starting_cash - Decimal("3000000")
    assert toast(response)["message"] == "P Target signed for $3.00M. Roster: Bulla."
    assert "On this roster" in response.text


def test_a_blocked_signing_explains_itself(signed_in, manager, signing_season):
    Roster.objects.create(manager=manager, season=signing_season, name="Bulla")
    pricey = make_player("Pricey", "C", 90_000_000)

    assert "Not enough cash ($63.00M left)." in player_page(signed_in, pricey)


def test_signing_into_someone_elses_roster_is_impossible(signed_in, other_manager, signing_season):
    theirs = Roster.objects.create(manager=other_manager, season=signing_season, name="Theirs")
    target = make_player("Target")

    response = signed_in.post(reverse("fantasy:roster-sign-player", args=[theirs.pk, target.pk]))

    assert response.status_code == 404
    assert theirs.memberships.count() == 0


def test_after_the_deadline_the_player_page_offers_a_trade(signed_in, manager, trading_season):
    roster = Roster.objects.create(manager=manager, season=trading_season, name="Bulla")
    target = make_player("Target")

    body = player_page(signed_in, target)

    trade_url = reverse("fantasy:roster-trade", args=[roster.pk])
    assert f"{trade_url}?in={target.pk}&amp;return_to=player" in body
    assert "Trade in…" in body


def test_a_trade_started_on_the_player_page_lands_on_the_roster(signed_in, manager, open_season):
    roster = Roster.objects.create(
        manager=manager, season=open_season, name="R", trades_available=1
    )
    out_player, in_player = make_player("Out"), make_player("In")
    services.buy(roster, out_player, Decimal("1000000"))

    response = signed_in.post(
        reverse("fantasy:roster-trade", args=[roster.pk])
        + f"?out={out_player.pk}&in={in_player.pk}&return_to=player"
    )

    assert response.status_code == 204
    assert response["HX-Redirect"] == reverse("fantasy:roster-build", args=[roster.pk])
    assert in_player in Roster.objects.get(pk=roster.pk).current_squad


def test_the_dialog_keeps_return_to_while_choosing(signed_in, full_roster):
    body = trade_dialog(signed_in, full_roster, return_to="player")

    assert "return_to=player" in body


def test_a_player_already_on_the_roster_is_marked(signed_in, manager, open_season):
    roster = Roster.objects.create(manager=manager, season=open_season, name="R")
    target = make_player("Mine")
    services.buy(roster, target, Decimal("1000000"))

    assert "On this roster" in player_page(signed_in, target)


def test_without_a_roster_the_player_page_points_at_creating_one(signed_in, open_season):
    body = player_page(signed_in, make_player("Anyone"))

    assert "You have no roster in the current season yet." in body


def test_the_player_page_links_to_compare_with_him(signed_in, db):
    target = make_player("Target")

    assert f"{reverse('nba:player-compare')}?player={target.slug}" in player_page(signed_in, target)


def test_only_your_own_rosters_are_offered(signed_in, other_manager, open_season):
    Roster.objects.create(manager=other_manager, season=open_season, name="Theirs")

    assert "Theirs" not in player_page(signed_in, make_player("Anyone"))


def test_rosters_from_other_seasons_are_not_offered(signed_in, manager, open_season):
    old = Season.objects.create(
        label="Old", starts_on=dt.date(2020, 10, 1), ends_on=dt.date(2021, 4, 1)
    )
    Roster.objects.create(manager=manager, season=old, name="Ancient")

    assert "Ancient" not in player_page(signed_in, make_player("Anyone"))
