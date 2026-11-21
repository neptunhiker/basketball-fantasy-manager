"""Correcting a past move in a real browser, on every screen size and in both themes.

The history page's row actions, the change-date modal with its live preview,
and the undo modal -- opened, used and photographed at phone, tablet and
desktop width. Screenshots land in tmp/screens/move-corrections/.
"""

import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.urls import reverse
from playwright.sync_api import expect

from ..move_correction_factories import World, at
from .conftest import VIEWPORTS, page_overflow, session_cookie, shot

FOLDER = "move-corrections"
User = get_user_model()

SQUAD = [
    ("Jalen", "Brunson", "G"),
    ("Devin", "Booker", "G"),
    ("Tyrese", "Haliburton", "G"),
    ("Cade", "Cunningham", "G"),
    ("Trae", "Young", "G"),
    ("Jayson", "Tatum", "F"),
    ("Paolo", "Banchero", "F"),
    ("Scottie", "Barnes", "F"),
    ("Lauri", "Markkanen", "F"),
    ("Franz", "Wagner", "F"),
    ("Nikola", "Jokić", "C"),
    ("Victor", "Wembanyama", "C"),
    ("Bam", "Adebayo", "C"),
    ("Chet", "Holmgren", "C"),
    ("Alperen", "Şengün", "C"),
]


@pytest.fixture
def history(django_db_serialized_rollback, transactional_db):
    from apps.fantasy.models import Manager
    from apps.nba.models import Player, Team

    call_command("seed_teams", verbosity=0)
    teams = list(Team.objects.order_by("name"))
    user = User.objects.create_user(email="coach@example.com", password="pw-for-tests")
    manager = Manager.objects.create(user=user, nick_name="Bulla")
    world = World(manager)

    def player(first, last, position, salary, index):
        return Player.objects.create(
            first_name=first,
            last_name=last,
            position=position,
            team=teams[index % len(teams)],
            current_salary=Decimal(salary),
        )

    squad = [
        player(first, last, position, 1_500_000 + (index * 290_000) % 3_000_000, index)
        for index, (first, last, position) in enumerate(SQUAD)
    ]
    for member in squad:
        world.sign(member)
    ball = player("LaMelo", "Ball", "G", 7_400_000, 20)
    mitchell = player("Donovan", "Mitchell", "G", 8_100_000, 21)
    siakam = player("Pascal", "Siakam", "F", 6_200_000, 22)

    first = world.trade(squad[0], ball, world.week1(2), hour=21)
    world.buy_trade(world.week1(3), hour=9)
    world.trade(squad[6], siakam, world.week2(1), hour=18)
    # Both sides priced in the second week, so moving the first trade there reprices it.
    world.snapshot(squad[0], at(world.week2(0), 22, 15), "6.1")
    world.snapshot(ball, at(world.week2(0), 22, 15), "7.9")
    return {
        "user": user,
        "world": world,
        "first": first,
        "mitchell": mitchell,
        "squad": squad,
    }


def open_history(browser, live_server, data, size, dark=False):
    context = browser.new_context(
        viewport=VIEWPORTS[size],
        color_scheme="dark" if dark else "light",
        device_scale_factor=2,
        is_mobile=size == "phone",
        has_touch=size in ("phone", "tablet"),
    )
    context.add_cookies([session_cookie(data["user"], live_server)])
    page = context.new_page()
    if dark:
        page.add_init_script("localStorage.setItem('theme', 'dark')")
    roster = data["world"].roster
    page.goto(live_server.url + reverse("fantasy:roster-history", args=[roster.pk]))
    page.wait_for_load_state("networkidle")
    return context, page


def visible_actions(page, size):
    """The row actions as this width shows them: icons in the table, labels on cards."""
    if size == "phone":
        return page.locator("ul.md\\:hidden")
    return page.locator("table")


@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
@pytest.mark.parametrize("size", ["phone", "tablet", "desktop"])
def test_the_history_offers_corrections_that_fit(browser, live_server, history, size, dark):
    context, page = open_history(browser, live_server, history, size, dark)
    theme = "dark" if dark else "light"

    actions = visible_actions(page, size)
    # Two trades and a bought trade: three rows, each with both actions.
    expect(actions.get_by_role("button", name="Change the date")).to_have_count(3)
    expect(actions.get_by_role("button", name="Undo")).to_have_count(3)
    assert page_overflow(page) == 0
    shot(page, FOLDER, f"history-{size}-{theme}")

    # Change the date: open, step one day later, see the verdict.
    actions.get_by_role("button", name="Change the date").last.click()
    modal = page.get_by_role("dialog")
    expect(modal).to_be_visible()
    expect(modal.get_by_text("Pick a day to see what changes.")).to_be_visible()
    expect(modal.get_by_role("button", name="Change date")).to_be_disabled()
    modal.get_by_role("button", name="One day later").click()
    expect(modal.get_by_text("Every move after it still works out.")).to_be_visible()
    expect(modal.get_by_role("button", name="Change date")).to_be_enabled()
    assert page_overflow(page) == 0
    shot(page, FOLDER, f"date-{size}-{theme}")
    page.keyboard.press("Escape")
    expect(modal).to_be_hidden()

    # Undo: open and look, but do not confirm.
    actions.get_by_role("button", name="Undo").last.click()
    modal = page.get_by_role("dialog")
    expect(modal.get_by_role("button", name="Undo trade")).to_be_visible()
    expect(modal.get_by_text("comes back to the roster")).to_be_visible()
    shot(page, FOLDER, f"undo-{size}-{theme}")
    context.close()


@pytest.mark.parametrize("size", ["phone", "desktop"])
def test_a_repriced_move_and_a_refusal_read_clearly(browser, live_server, history, size):
    context, page = open_history(browser, live_server, history, size)
    world = history["world"]
    actions = visible_actions(page, size)

    # The oldest trade (Brunson → Ball) is the last row: newest first.
    actions.get_by_role("button", name="Change the date").last.click()
    modal = page.get_by_role("dialog")
    day = modal.locator('input[type="date"]')
    day.fill(world.week2(2).isoformat())
    day.dispatch_event("change")
    expect(modal.get_by_text("Repriced")).to_be_visible()
    expect(modal.get_by_text("on the other side of the salary update")).to_be_visible()
    shot(page, FOLDER, f"repriced-{size}")

    # Before the season: refused, with the reason and no way to confirm.
    day.fill((world.season.starts_on - dt.timedelta(days=3)).isoformat())
    day.dispatch_event("change")
    expect(modal.get_by_text("This would not work out")).to_be_visible()
    expect(modal.get_by_role("button", name="Change date")).to_be_disabled()
    shot(page, FOLDER, f"refused-{size}")
    context.close()


@pytest.mark.parametrize("size", ["phone", "desktop"])
def test_moving_a_trade_updates_the_history(browser, live_server, history, size):
    context, page = open_history(browser, live_server, history, size)
    world = history["world"]
    actions = visible_actions(page, size)

    actions.get_by_role("button", name="Change the date").last.click()
    modal = page.get_by_role("dialog")
    modal.get_by_role("button", name="One day later").click()
    expect(modal.get_by_role("button", name="Change date")).to_be_enabled()
    modal.get_by_role("button", name="Change date").click()

    page.wait_for_url("**/history/")
    page.wait_for_load_state("networkidle")
    expect(page.get_by_text("moved to").first).to_be_visible()
    expect(visible_actions(page, size).get_by_text("Date changed")).to_have_count(1)
    expect(page.get_by_text("adds up to exactly")).to_be_visible()
    shot(page, FOLDER, f"after-move-{size}")

    history["first"].refresh_from_db()
    assert history["first"].occurred_at == at(world.week1(3), 21)
    context.close()


def test_undoing_a_trade_brings_the_player_back(browser, live_server, history):
    from apps.fantasy.models import RosterPlayer

    context, page = open_history(browser, live_server, history, "desktop")
    page.locator("table").get_by_role("button", name="Undo").first.click()
    modal = page.get_by_role("dialog")
    modal.get_by_role("button", name="Undo trade").click()

    page.wait_for_url("**/history/")
    expect(page.get_by_text("undone.").first).to_be_visible()
    banchero = history["squad"][6]
    assert RosterPlayer.objects.open().filter(player=banchero).exists()
    shot(page, FOLDER, "after-undo-desktop")
    context.close()


@pytest.mark.parametrize("size", ["phone", "tablet", "desktop"])
def test_the_corrections_read_well_in_german(browser, live_server, history, size):
    """German runs longer than English: the same screens, checked for fit."""
    user = history["user"]
    user.language = "de"
    user.save(update_fields=["language"])
    context, page = open_history(browser, live_server, history, size)
    actions = visible_actions(page, size)

    expect(actions.get_by_role("button", name="Datum ändern")).to_have_count(3)
    assert page_overflow(page) == 0
    shot(page, FOLDER, f"de-history-{size}")

    actions.get_by_role("button", name="Datum ändern").last.click()
    modal = page.get_by_role("dialog")
    modal.get_by_role("button", name="Einen Tag später").click()
    expect(modal.get_by_text("Alle späteren Moves gehen weiterhin auf.")).to_be_visible()
    assert page_overflow(page) == 0
    shot(page, FOLDER, f"de-date-{size}")
    page.keyboard.press("Escape")
    expect(modal).to_be_hidden()

    actions.get_by_role("button", name="Rückgängig").last.click()
    modal = page.get_by_role("dialog")
    expect(modal.get_by_role("button", name="Trade rückgängig machen")).to_be_visible()
    shot(page, FOLDER, f"de-undo-{size}")
    context.close()
