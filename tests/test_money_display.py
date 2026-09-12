"""Money is written in millions everywhere -- the filter, and the screens that
use it.
"""

import datetime as dt
from decimal import Decimal

import pytest
from django.template import Context, Template
from django.urls import reverse

from apps.core.templatetags.money import MISSING, exact, millions
from apps.fantasy import services
from apps.fantasy.models import Roster, Season
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
    roster = Roster.objects.create(manager=manager, season=season, name="Roster 1")
    player = Player.objects.create(
        first_name="Nikola", last_name="Jokic", position="C", current_salary=Decimal("18000000")
    )
    services.buy(roster, player, player.current_salary)
    return roster


# --- the filter --------------------------------------------------------------


@pytest.mark.parametrize(
    "amount,expected",
    [
        ("60000000", "$60.00M"),
        ("18000000", "$18.00M"),
        ("9600000", "$9.60M"),
        ("1234567", "$1.23M"),
        ("500000", "$0.50M"),
        ("0", "$0.00M"),
        # The sign goes outside the currency symbol, so a cash flow column reads
        # as a direction rather than as a strange number.
        ("-6550000", "-$6.55M"),
    ],
)
def test_millions_rounds_to_two_decimals(amount, expected):
    assert millions(Decimal(amount)) == expected


def test_millions_survives_a_missing_amount():
    """A player without a salary must not blow up a whole page."""
    assert millions(None) == MISSING
    assert millions("") == MISSING
    assert millions("nonsense") == MISSING


def test_exact_groups_thousands_for_the_hover_title():
    assert exact(Decimal("60000000")) == "$60,000,000"
    assert exact(Decimal("-6550000")) == "-$6,550,000"
    assert exact(None) == ""


def test_the_currency_is_swappable():
    assert millions(Decimal("1000000"), "€") == "€1.00M"


def test_the_filter_loads_in_a_template():
    rendered = Template("{% load money %}{{ v|millions }}").render(
        Context({"v": Decimal("60000000")})
    )
    assert rendered == "$60.00M"


# --- the screens -------------------------------------------------------------


def test_no_screen_prints_a_raw_eight_figure_amount(signed_in, roster):
    """The whole point: 60000000 and 6000000 are indistinguishable at a glance."""
    for url in [
        reverse("fantasy:roster-list"),
        reverse("fantasy:roster-rules", args=[roster.pk]),
        reverse("fantasy:roster-build", args=[roster.pk]),
    ]:
        body = signed_in.get(url).content.decode()
        assert "M" in body, url
        # The raw digits may only survive inside a hover title.
        for line in body.splitlines():
            if "42000000" in line or "18000000" in line:
                assert "title=" in line, (url, line.strip())


def test_the_build_screen_shows_cash_squad_value_and_salaries_in_millions(signed_in, roster):
    body = signed_in.get(reverse("fantasy:roster-build", args=[roster.pk])).content.decode()

    assert "$42.00M" in body  # cash left after an 18M signing
    assert "$18.00M" in body  # the player's own salary, in the squad list

    # Squad value is a labelled figure in the team card's stats band, so the
    # label and the number are separate elements. Checked as "the figure
    # follows its label" rather than as a substring of the page: $18.00M is
    # also this player's salary, so a bare `in body` would pass either way.
    _, _, after_label = body.partition("Squad value")
    assert "$18.00M" in after_label[:400], "squad value figure not next to its label"


def test_the_roster_card_shows_cash_in_millions(signed_in, roster):
    body = signed_in.get(reverse("fantasy:roster-list")).content.decode()
    assert "$42.00M" in body


def test_the_picker_shows_salaries_in_millions(signed_in, roster):
    Player.objects.create(
        first_name="Cheap", last_name="Guy", position="G", current_salary=Decimal("750000")
    )
    body = signed_in.get(reverse("fantasy:roster-build", args=[roster.pk])).content.decode()
    assert "$0.75M" in body


def test_the_exact_filter_keeps_cents_only_where_there_are_any():
    """A derived figure has a remainder; a salary does not."""
    assert exact(Decimal("60000000.00")) == "$60,000,000"
    assert exact(Decimal("9286666.67")) == "$9,286,666.67"
