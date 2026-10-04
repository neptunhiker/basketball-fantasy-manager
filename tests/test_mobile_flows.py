"""Phase 3 of the mobile plan: the key flows on phones (template/view level).

The browser checks in tests/browser/test_mobile_flows.py look at the result.
"""

import datetime as dt
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.fantasy import services
from apps.fantasy.models import Roster, Season
from apps.nba.models import Player


@pytest.fixture
def signed_in(client, user, password):
    client.login(email=user.email, password=password)
    return client


@pytest.fixture
def roster(manager, db):
    today = timezone.localdate()
    season = Season.objects.create(
        label="Live",
        starts_on=today - dt.timedelta(days=30),
        ends_on=today + dt.timedelta(days=150),
        is_current=True,
    )
    roster = Roster.objects.create(manager=manager, season=season, name="R", trades_available=1)
    for n, position in enumerate("GFC"):
        player = Player.objects.create(
            first_name="P", last_name=f"Squad{n}", position=position, current_salary=1_000_000
        )
        services.buy(roster, player, Decimal("1000000"))
    Player.objects.create(first_name="P", last_name="Free", position="G", current_salary=900_000)
    return roster


# --- players list ------------------------------------------------------------------


def test_the_filter_button_counts_active_filters(signed_in, db):
    response = signed_in.get(
        reverse("nba:player-list"), {"position": "G", "inactive": "1", "q": "x"}
    )

    assert response.context["active_filter_count"] == 2  # the search does not count
    assert 'aria-controls="player-filters"' in response.text
    assert 'id="player-filters" x-show="filtersOpen"' in response.text


def test_the_chart_folds_on_phones_and_keeps_its_state_outside_the_swap(signed_in, db):
    body = signed_in.get(reverse("nba:player-list")).text

    assert 'x-data="{ chartOpen: false }"' in body
    assert 'id="player-chart" x-show="chartOpen"' in body
    assert "md:!block" in body
    # The state sits on the card, outside #player-table, which filters replace.
    assert body.index("chartOpen: false") < body.index('<div id="player-table">')


def test_the_team_page_gives_its_chart_a_state_too(signed_in, db):
    from apps.nba.models import Team

    Player.objects.create(
        first_name="P", last_name="Heat", position="G", team=Team.objects.get(abbreviation="MIA")
    )

    body = signed_in.get(reverse("nba:team-detail", args=["mia"])).text

    assert 'x-data="{ chartOpen: false }"' in body
    assert 'id="player-chart"' in body


# --- trade dialog ------------------------------------------------------------------


def trade_body(client, roster, **params):
    query = "".join(f"&{key}={value}" for key, value in params.items())
    return client.get(reverse("fantasy:roster-trade", args=[roster.pk]) + "?body=1" + query)


def test_the_trade_starts_at_step_one_on_phones(signed_in, roster):
    response = trade_body(signed_in, roster)

    assert response.context["mobile_step"] == 1
    assert "Step 1 of 2" in response.text


def test_choosing_the_player_out_moves_to_step_two(signed_in, roster):
    out = roster.current_squad[0]
    response = trade_body(signed_in, roster, out=out.pk)

    assert response.context["mobile_step"] == 2
    assert "Step 2 of 2" in response.text
    assert f"Change player out ({out.full_name})" in response.text


def test_a_preselected_incoming_player_is_named_in_step_one(signed_in, roster):
    free = Player.objects.get(last_name="Free")
    response = trade_body(signed_in, roster, **{"in": free.pk})

    assert response.context["mobile_step"] == 1
    assert "Coming in: P Free" in response.text


def test_the_summary_rides_in_the_pinned_button_bar(signed_in, roster):
    out = roster.current_squad[0]
    free = Player.objects.get(last_name="Free")
    body = trade_body(signed_in, roster, out=out.pk, **{"in": free.pk}).text

    bar = body[body.index('<div class="modal-actions">') :]
    assert "Cash after" in bar
    assert "P Free" in bar


# --- dashboard ----------------------------------------------------------------------


def test_the_countdown_is_one_row_and_quiet_for_screen_readers(signed_in, db):
    now = timezone.now()
    Season.objects.create(
        label="Soon",
        starts_on=timezone.localdate() + dt.timedelta(days=20),
        ends_on=timezone.localdate() + dt.timedelta(days=200),
        is_current=True,
        signings_open_at=now - dt.timedelta(days=1),
        signings_close_at=now + dt.timedelta(days=5),
    )

    body = signed_in.get(reverse("core:dashboard")).text

    assert 'class="mt-3 grid grid-cols-4' in body
    assert 'aria-hidden="true" data-target=' in body
    assert "countdown-reloaded:" in body
    main = body[body.index("<main") :]
    assert "<h1" not in main


# --- player page --------------------------------------------------------------------


def test_the_player_page_has_a_back_arrow_on_phones(signed_in, db):
    player = Player.objects.create(first_name="P", last_name="Page", position="G")

    body = signed_in.get(reverse("nba:player-detail", args=[player.slug])).text

    header = body[: body.index("<main")]
    assert 'aria-label="All players"' in header
    assert 'class="btn btn-secondary hidden sm:inline-flex">All players' in body
