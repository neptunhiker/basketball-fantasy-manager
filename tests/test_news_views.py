"""The news page: what it lists, how its filters narrow it, what it costs."""

import datetime as dt
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.fantasy import services
from apps.fantasy.models import Roster, Season
from apps.nba.models import Player, PlayerNews, Team

NEWS_URL = reverse("nba:news")


@pytest.fixture
def signed_in(client, user, password, db):
    client.login(email=user.email, password=password)
    return client


@pytest.fixture
def season(db):
    now = timezone.now()
    return Season.objects.create(
        label="2026-27",
        starts_on=dt.date(2026, 10, 20),
        ends_on=dt.date(2027, 4, 11),
        is_current=True,
        signings_open_at=now - dt.timedelta(days=5),
        signings_close_at=now + dt.timedelta(days=5),
    )


@pytest.fixture
def teams(db):
    return {
        code: Team.objects.get_or_create(abbreviation=code, defaults={"name": name})[0]
        for code, name in [
            ("HOU", "Houston Rockets"),
            ("PHX", "Phoenix Suns"),
            ("MIN", "Minnesota Timberwolves"),
        ]
    }


_ids = iter(range(600000, 700000))


def news(headline, player=None, team="HOU", kind=PlayerNews.Kind.NEWS, days_ago=0, name=None):
    return PlayerNews.objects.create(
        rotowire_news_id=next(_ids),
        player=player,
        kind=kind,
        headline=headline,
        body=f"{headline}, in more words.",
        url=f"https://www.rotowire.com/basketball/headlines/x-news-y-{next(_ids)}",
        published_on=timezone.localdate() - dt.timedelta(days=days_ago),
        feed_player_name=name or (player.full_name if player else "Somebody Else"),
        feed_team_abbreviation=team,
        feed_position="G",
    )


def player(first, last, team, salary="2000000"):
    return Player.objects.create(
        first_name=first,
        last_name=last,
        position="G",
        team=team,
        current_salary=Decimal(salary),
    )


def headlines(response):
    return [item.headline for item in response.context["news"]]


def test_the_page_needs_a_login(client, db):
    response = client.get(NEWS_URL)

    assert response.status_code == 302
    assert reverse("accounts:login") in response.url


def test_the_menu_links_to_the_page(signed_in):
    response = signed_in.get(reverse("core:dashboard"))

    assert f'href="{NEWS_URL}"'.encode() in response.content


def test_news_is_listed_newest_first_and_grouped_by_day(signed_in, teams):
    news("Older item", days_ago=1)
    news("First today")
    news("Second today")

    response = signed_in.get(NEWS_URL)

    assert headlines(response) == ["Second today", "First today", "Older item"]
    page = response.content.decode()
    assert page.index("Today") < page.index("Second today")
    assert page.index("Yesterday") < page.index("Older item")


def test_a_matched_player_is_linked_and_an_unmatched_one_is_not(signed_in, teams):
    smart = player("Marcus", "Smart", teams["HOU"])
    news("Questionable", player=smart)
    news("Ruled out", name="Khalifa Diop", team="CLE")

    response = signed_in.get(NEWS_URL)

    page = response.content.decode()
    assert reverse("nba:player-detail", args=[smart.slug]) in page
    feed = page[page.index('id="news-feed"') :]
    assert "Khalifa Diop" in feed
    assert feed.count('href="/players/') == 1


def test_the_article_opens_on_rotowire_in_a_new_tab(signed_in, teams):
    item = news("Questionable")

    page = signed_in.get(NEWS_URL).content.decode()

    assert f'href="{item.url}" target="_blank" rel="noopener noreferrer"' in page


def test_rotowire_team_codes_show_as_ours(signed_in, teams):
    news("Bottled up", team="PHO")

    page = signed_in.get(NEWS_URL).content.decode()

    assert "G · PHX" in page
    assert "PHO" not in page


def test_the_kind_filter_keeps_injuries_or_news(signed_in, teams):
    news("Out with a knee", kind=PlayerNews.Kind.INJURY)
    news("Joins the starting lineup")

    injuries = signed_in.get(NEWS_URL, {"kind": "injury"})
    other = signed_in.get(NEWS_URL, {"kind": "news"})
    unknown = signed_in.get(NEWS_URL, {"kind": "gossip"})

    assert headlines(injuries) == ["Out with a knee"]
    assert headlines(other) == ["Joins the starting lineup"]
    assert len(headlines(unknown)) == 2


def test_the_team_filter_takes_the_feed_s_team_and_the_player_s(signed_in, teams):
    signed = player("Enrique", "Freeman", teams["MIN"])
    news("Re-signs with Minnesota", player=signed, team="FA")
    news("Wolves win", team="MIN")
    news("Suns lose", team="PHO")
    news("Rockets win", team="HOU")

    minnesota = signed_in.get(NEWS_URL, {"team": "MIN"})
    phoenix = signed_in.get(NEWS_URL, {"team": "PHX"})

    assert headlines(minnesota) == ["Wolves win", "Re-signs with Minnesota"]
    assert headlines(phoenix) == ["Suns lose"]


def test_the_roster_filter_keeps_players_on_my_rosters(signed_in, teams, season, manager):
    mine = player("Devin", "Booker", teams["PHX"])
    sold = player("Jalen", "Green", teams["PHX"])
    other = player("Marcus", "Smart", teams["HOU"])
    roster = Roster.objects.create(manager=manager, season=season, name="Bulla Ballers")
    services.buy(roster, mine, mine.current_salary)
    services.buy(roster, sold, sold.current_salary)
    services.sell(roster, sold, sold.current_salary)
    news("Booker scores", player=mine, team="PHO")
    news("Green scores", player=sold, team="PHO")
    news("Smart scores", player=other)
    news("Nobody we know")

    response = signed_in.get(NEWS_URL, {"roster": "mine"})

    assert headlines(response) == ["Booker scores"]
    assert response.context["news"][0].on_rosters == ["Bulla Ballers"]


def test_one_roster_of_several_can_be_picked(signed_in, teams, season, manager):
    first = player("Devin", "Booker", teams["PHX"])
    second = player("Marcus", "Smart", teams["HOU"])
    roster_a = Roster.objects.create(manager=manager, season=season, name="A")
    roster_b = Roster.objects.create(manager=manager, season=season, name="B")
    services.buy(roster_a, first, first.current_salary)
    services.buy(roster_b, second, second.current_salary)
    news("Booker scores", player=first)
    news("Smart scores", player=second)

    response = signed_in.get(NEWS_URL, {"roster": str(roster_b.pk)})

    assert headlines(response) == ["Smart scores"]
    page = response.content.decode()
    assert f'value="{roster_a.pk}"' in page


def test_somebody_else_s_roster_is_not_a_filter(signed_in, teams, season, manager, other_manager):
    theirs = Roster.objects.create(manager=other_manager, season=season, name="Rival")
    rival_player = player("Devin", "Booker", teams["PHX"])
    services.buy(theirs, rival_player, rival_player.current_salary)
    news("Booker scores", player=rival_player)
    news("Somebody else scores")

    response = signed_in.get(NEWS_URL, {"roster": str(theirs.pk)})

    assert len(headlines(response)) == 2
    assert response.context["news"][0].on_rosters == []


def test_without_a_roster_the_roster_filter_is_disabled(signed_in, teams):
    news("Smart scores")

    page = signed_in.get(NEWS_URL).content.decode()

    assert "You have no roster this season." in page


def test_filters_combine(signed_in, teams):
    news("Rockets injury", kind=PlayerNews.Kind.INJURY)
    news("Rockets news")
    news("Suns injury", team="PHX", kind=PlayerNews.Kind.INJURY)

    response = signed_in.get(NEWS_URL, {"team": "HOU", "kind": "injury"})

    assert headlines(response) == ["Rockets injury"]


def test_an_htmx_request_gets_only_the_list(signed_in, teams):
    news("Smart scores")

    response = signed_in.get(NEWS_URL, {"kind": "news"}, HTTP_HX_REQUEST="true")

    assert response.templates[0].name == "nba/partials/news_feed.html"
    assert b'id="news-feed"' in response.content
    assert b"<html" not in response.content


def test_the_empty_states(signed_in, db):
    empty = signed_in.get(NEWS_URL).content.decode()
    filtered = signed_in.get(NEWS_URL, {"kind": "injury"}).content.decode()

    assert "No news yet." in empty
    assert "Nothing matches those filters." in filtered


def test_the_list_is_paginated(signed_in, teams):
    for index in range(55):
        news(f"Item {index}")

    first = signed_in.get(NEWS_URL)
    second = signed_in.get(NEWS_URL, {"page": 2})

    assert len(headlines(first)) == 50
    assert len(headlines(second)) == 5
    assert "55 items · page 1 of 2" in first.content.decode()


def test_the_last_update_is_shown(signed_in, teams):
    news("Smart scores")

    assert "Last update:" in signed_in.get(NEWS_URL).content.decode()


def test_the_page_costs_the_same_however_many_items(signed_in, teams, season, manager):
    roster = Roster.objects.create(manager=manager, season=season, name="Bulla Ballers")

    def add_items(count, label):
        for index in range(count):
            someone = player("P", f"{label} {index}", teams["HOU"])
            if index < 2:
                services.buy(roster, someone, someone.current_salary)
            news(f"{label} {index}", player=someone)

    add_items(2, "Few")
    with CaptureQueriesContext(connection) as few:
        signed_in.get(NEWS_URL)
    add_items(20, "Many")
    with CaptureQueriesContext(connection) as many:
        signed_in.get(NEWS_URL)

    assert len(many) == len(few)


def test_the_admin_lists_news_and_filters_the_unmatched(admin_client, teams):
    news("Matched", player=player("Marcus", "Smart", teams["HOU"]))
    unmatched = news("Unmatched")

    listing = admin_client.get(reverse("admin:nba_playernews_changelist"), {"matched": "no"})
    detail = admin_client.get(reverse("admin:nba_playernews_change", args=[unmatched.pk]))

    assert listing.status_code == 200
    assert list(listing.context["cl"].queryset) == [unmatched]
    assert detail.status_code == 200
