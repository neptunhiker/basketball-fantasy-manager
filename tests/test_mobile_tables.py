"""Phase 2 of the mobile plan: tables that work on a phone (template-level checks).

The browser checks in tests/browser/test_mobile_tables.py look at the result.
"""

import re
from collections import Counter
from decimal import Decimal

import pytest
from django.urls import reverse

from apps.fantasy import services
from apps.fantasy.models import Roster, Season
from apps.nba.models import Player


@pytest.fixture
def signed_in(client, user, password):
    client.login(email=user.email, password=password)
    return client


@pytest.fixture
def roster(manager, db):
    import datetime as dt

    from django.utils import timezone

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
    return roster


def test_the_squad_comes_as_cards_on_phones_and_a_table_from_md(signed_in, roster):
    body = signed_in.get(reverse("fantasy:roster-build", args=[roster.pk])).text

    cards = body[body.index('<div class="md:hidden">') :]
    assert cards.count(">Trade</button>") == 3
    assert cards.count(">Release</button>") == 3
    assert 'id="squad-sort"' in cards
    assert '<div class="hidden overflow-x-auto md:block">' in body


def test_the_card_sort_list_marks_the_current_sort(signed_in, roster):
    body = signed_in.get(reverse("fantasy:roster-build", args=[roster.pk]), {"sort": "salary"}).text

    assert '<option value="salary" selected>' in body


def test_no_html_id_appears_twice_on_the_build_page(signed_in, roster):
    body = signed_in.get(reverse("fantasy:roster-build", args=[roster.pk])).text

    ids = Counter(re.findall(r'\sid="([^"{]+)"', body))
    assert not [name for name, count in ids.items() if count > 1]


def test_the_history_comes_as_cards_on_phones(signed_in, roster):
    body = signed_in.get(reverse("fantasy:roster-history", args=[roster.pk])).text

    assert "md:hidden" in body
    assert "Balance" in body
    assert '<div class="hidden overflow-x-auto md:block">' in body


def test_the_comparison_is_transposed_on_phones(signed_in, roster):
    slugs = [p.slug for p in Player.objects.all()[:2]]
    response = signed_in.get(
        reverse("nba:player-compare") + f"?player={slugs[0]}&player={slugs[1]}"
    )

    keys = [key for key, _ in response.context["compare_rows"]]
    assert keys[:5] == ["team", "position", "salary", "expected", "value"]
    text = response.text
    phone = text[
        text.index('<div class="overflow-x-auto md:hidden">') : text.index(
            '<div class="hidden overflow-x-auto md:block">'
        )
    ]
    assert '<th scope="row"' in phone
    assert phone.count("Remove P Squad") == 2


def test_the_players_table_keeps_names_in_view_and_drops_columns(signed_in, roster):
    body = signed_in.get(reverse("nba:player-list")).text

    assert "max-md:sticky max-md:left-12" in body
    assert body.count("hidden px-4 py-3 text-right font-medium md:table-cell") == 2


@pytest.mark.parametrize(
    ("url_name", "staff", "marker"),
    [
        ("fantasy:season-list", True, "max-sm:[&amp;_tr&gt;*:nth-child(6)]:hidden"),
        ("accounts:user-list", True, "max-sm:[&amp;_tr&gt;*:nth-child(3)]:hidden"),
        ("nba:team-list", False, "max-sm:[&amp;_tr&gt;*:nth-child(4)]:hidden"),
    ],
)
def test_secondary_columns_are_hidden_on_phones(client, user, password, url_name, staff, marker):
    if staff:
        user.is_staff = user.is_superuser = True
        user.save()
    client.login(email=user.email, password=password)
    Season.objects.get_or_create(
        label="S", defaults={"starts_on": "2026-10-01", "ends_on": "2027-04-01"}
    )

    body = client.get(reverse(url_name)).text

    assert marker in body or marker.replace("&amp;", "&").replace("&gt;", ">") in body
