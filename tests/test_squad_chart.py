"""The build page's chart: the squad's points per game against salary."""

import datetime as dt
from decimal import Decimal

import pytest
from django.urls import reverse

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
        starting_cash=Decimal("60000000"),
    )


@pytest.fixture
def signed_in(client, user, password, db):
    client.login(email=user.email, password=password)
    return client


@pytest.fixture
def roster(season, manager):
    return Roster.objects.create(manager=manager, season=season, name="Roster 1")


def player(name, salary, fp_per_game):
    return Player.objects.create(
        first_name=name,
        last_name="Test",
        position="C",
        current_salary=Decimal(salary),
        current_fp_per_game=fp_per_game,
    )


def build_page(client, roster):
    return client.get(reverse("fantasy:roster-build", args=[roster.pk])).content.decode()


def test_the_chart_plots_the_squad_and_not_the_market(signed_in, roster):
    services.buy(roster, player("Signed", 10_000_000, Decimal("30")), Decimal("10000000"))
    player("Market", 5_000_000, Decimal("20"))

    body = build_page(signed_in, roster)
    _, _, chart = body.partition('id="squad-chart"')
    chart, _, _ = chart.partition('id="player-picker"')

    assert 'data-label="Signed Test"' in chart
    assert 'data-label="Market Test"' not in chart
    assert "Fair value" in chart


def test_an_empty_squad_says_why_there_is_no_chart(signed_in, roster):
    body = build_page(signed_in, roster)

    assert 'id="squad-chart"' in body
    assert "The comparison appears when players on this squad" in body


def test_a_signing_redraws_the_chart_out_of_band(signed_in, roster):
    signed = player("Signed", 10_000_000, Decimal("30"))

    response = signed_in.post(reverse("fantasy:roster-buy", args=[roster.pk, signed.pk]))
    body = response.content.decode()

    assert 'id="squad-chart" class="card px-4 pb-4 pt-5" hx-swap-oob="outerHTML"' in body
    assert 'data-label="Signed Test"' in body
