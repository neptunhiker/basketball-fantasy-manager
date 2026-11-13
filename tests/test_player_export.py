"""The player list as an Excel file: the table on screen, every page of it."""

from decimal import Decimal
from io import BytesIO

import pytest
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from openpyxl import load_workbook

from apps.fantasy.models import WatchlistEntry
from apps.nba import player_export
from apps.nba.models import Player, Team

pytestmark = pytest.mark.django_db


@pytest.fixture
def teams(db):
    call_command("seed_teams", verbosity=0)


@pytest.fixture
def signed_in(client, user, password):
    client.login(email=user.email, password=password)
    return client


def make(first, last, position="G", team="LAL", salary=5_000_000, fp=None, **extra):
    return Player.objects.create(
        first_name=first,
        last_name=last,
        position=position,
        team=Team.objects.get(abbreviation=team) if team else None,
        current_salary=Decimal(salary),
        current_fp_per_game=None if fp is None else Decimal(str(fp)),
        **extra,
    )


@pytest.fixture
def players(teams):
    return {
        "James": make("LeBron", "James", "F", "LAL", 12_500_000, 41.2),
        "Doncic": make("Luka", "Doncic", "G", "LAL", 14_000_000, 50.1),
        "Tatum": make("Jayson", "Tatum", "F", "BOS", 11_000_000, 38.0),
        "White": make("Derrick", "White", "G", "BOS", 6_000_000, 25.5),
        "Guy": make("Retired", "Guy", "C", None, 1_000_000, None, is_active=False),
    }


def workbook(response):
    assert response.status_code == 200, response.content[:300]
    return load_workbook(BytesIO(response.content))


def table(response):
    """The data rows as dicts keyed by header, and the sheet."""
    sheet = workbook(response).worksheets[0]
    header_row = player_export.HEADER_ROW
    headers = [cell.value for cell in sheet[header_row]]
    rows = []
    for row in sheet.iter_rows(min_row=player_export.FIRST_DATA_ROW):
        values = dict(zip(headers, (cell.value for cell in row), strict=True))
        if values.get("Player"):
            rows.append(values)
    return rows, sheet


def names(response):
    return [row["Player"] for row in table(response)[0]]


def export(client, **params):
    return client.get(reverse("nba:player-export"), params)


# --- the response ---------------------------------------------------------------


def test_requires_login(client, players):
    response = export(client)
    assert response.status_code == 302
    assert reverse("accounts:login") in response.url


def test_is_an_xlsx_attachment_kept_out_of_shared_caches(signed_in, players):
    response = export(signed_in)

    assert response["Content-Type"] == player_export.CONTENT_TYPE
    disposition = response["Content-Disposition"]
    assert disposition.startswith('attachment; filename="players-')
    assert disposition.endswith('.xlsx"')
    assert "no-store" in response["Cache-Control"]


def test_has_a_title_block_and_a_legend(signed_in, players):
    book = workbook(export(signed_in, position="G"))
    sheet = book.worksheets[0]

    assert sheet["A1"].value == "Players"
    assert "2 players" in sheet["A2"].value
    assert sheet["A3"].value == "Filters: Position: Guard"
    assert sheet["A4"].value == "Sorted by: Avg/game, highest first"
    assert sheet.freeze_panes == "C7"
    assert sheet.auto_filter.ref == "A6:" + sheet.auto_filter.ref.split(":")[1]
    assert book.sheetnames == ["Players", "Legend"]
    legend = [row[0] for row in book["Legend"].iter_rows(min_row=4, values_only=True)]
    assert "Value" in legend and "Hotness" in legend


def test_an_empty_result_still_downloads(signed_in, players):
    rows, sheet = table(export(signed_in, q="nobody-by-that-name"))

    assert rows == []
    assert sheet.cell(player_export.FIRST_DATA_ROW, 1).value == "Nothing matches those filters."


# --- the same rows as the table -------------------------------------------------


def test_follows_the_filters(signed_in, players):
    assert names(export(signed_in, position="G")) == ["Luka Doncic", "Derrick White"]
    assert names(export(signed_in, team="BOS")) == ["Jayson Tatum", "Derrick White"]
    assert names(export(signed_in, q="james")) == ["LeBron James"]


def test_leaves_inactive_players_out_unless_asked(signed_in, players):
    assert "Retired Guy" not in names(export(signed_in))

    rows, _sheet = table(export(signed_in, inactive="1"))

    by_name = {row["Player"]: row for row in rows}
    assert by_name["Retired Guy"]["Status"] == "Inactive"
    assert by_name["Retired Guy"]["Team"] == "Free Agent"


def test_only_shows_the_status_column_with_inactive_players(signed_in, players):
    rows, _sheet = table(export(signed_in))
    assert "Status" not in rows[0]


def test_follows_the_sort(signed_in, players):
    assert names(export(signed_in, sort="salary", dir="desc")) == [
        "Luka Doncic",
        "LeBron James",
        "Jayson Tatum",
        "Derrick White",
    ]
    assert names(export(signed_in, sort="name", dir="asc"))[0] == "Luka Doncic"


def test_the_hotness_sort_works_too(signed_in, players):
    # Sorted in Python rather than the database: a list, not a queryset.
    response = export(signed_in, sort="hotness")
    assert len(names(response)) == 4
    assert table(response)[1]["A4"].value == "Sorted by: Hotness, highest first"


def test_exports_every_page_not_just_the_first(signed_in, teams):
    lakers = Team.objects.get(abbreviation="LAL")
    Player.objects.bulk_create(
        Player(
            first_name="P",
            last_name=f"Number{index:02}",
            slug=f"p-{index}",
            position="G",
            team=lakers,
        )
        for index in range(60)
    )

    assert len(names(export(signed_in))) == 60


def test_ignores_the_page_parameter(signed_in, players):
    assert len(names(export(signed_in, page="2"))) == 4


# --- the cells ------------------------------------------------------------------


def test_figures_are_numbers_with_a_display_format(signed_in, players):
    rows, sheet = table(export(signed_in, q="tatum"))
    row = rows[0]

    assert row["Actual salary"] == pytest.approx(11.0)  # millions
    assert row["Avg/game"] == pytest.approx(38.0)
    assert isinstance(row["Expected salary"], int | float)
    headers = [cell.value for cell in sheet[player_export.HEADER_ROW]]
    salary = sheet.cell(player_export.FIRST_DATA_ROW, headers.index("Actual salary") + 1)
    assert salary.number_format == player_export.MONEY
    value = sheet.cell(player_export.FIRST_DATA_ROW, headers.index("Value") + 1)
    assert value.number_format == player_export.SIGNED_MONEY


def test_the_name_links_to_the_player_page(signed_in, players):
    _rows, sheet = table(export(signed_in, q="tatum"))
    link = sheet.cell(player_export.FIRST_DATA_ROW, 2).hyperlink.target

    assert link == "http://testserver" + reverse("nba:player-detail", args=[players["Tatum"].slug])


def test_a_healthy_player_reads_healthy_without_a_return_date(signed_in, players):
    row = table(export(signed_in, q="white"))[0][0]

    assert row["Injury"] == "Healthy"
    assert row["Return"] is None


def test_headers_follow_the_users_language(signed_in, players, user):
    user.language = "de"
    user.save(update_fields=["language"])

    response = export(signed_in)
    sheet = workbook(response).worksheets[0]
    headers = [cell.value for cell in sheet[player_export.HEADER_ROW]]

    assert "Spieler" in headers
    assert "Aktuelles Gehalt" in headers
    assert sheet.title == "Spieler"
    assert 'filename="spieler-' in response["Content-Disposition"]


# --- marks are the viewer's own -------------------------------------------------


def test_marks_show_and_only_the_viewers_own(signed_in, players, user, other_manager):
    WatchlistEntry.objects.create(
        user=user, player=players["Tatum"], stance=WatchlistEntry.Stance.WATCH
    )
    WatchlistEntry.objects.create(
        user=user, player=players["White"], stance=WatchlistEntry.Stance.KICK
    )
    WatchlistEntry.objects.create(
        user=other_manager.user, player=players["James"], stance=WatchlistEntry.Stance.WATCH
    )

    marks = {row["Player"]: row["Watchlist"] for row in table(export(signed_in))[0]}

    assert marks["Jayson Tatum"] == "★ Watched"
    assert marks["Derrick White"] == "Kick list"
    assert marks["LeBron James"] is None


def test_the_watchlist_exports_only_starred_players(signed_in, players, user):
    WatchlistEntry.objects.create(
        user=user, player=players["Tatum"], stance=WatchlistEntry.Stance.WATCH
    )

    response = signed_in.get(reverse("nba:watchlist-export"))

    assert names(response) == ["Jayson Tatum"]
    assert 'filename="watchlist-' in response["Content-Disposition"]
    assert workbook(response).worksheets[0]["A1"].value == "Watchlist"


def test_the_kick_list_filter_names_the_file(signed_in, players, user):
    WatchlistEntry.objects.create(
        user=user, player=players["White"], stance=WatchlistEntry.Stance.KICK
    )

    response = export(signed_in, kicklist="1")

    assert names(response) == ["Derrick White"]
    assert workbook(response).worksheets[0]["A1"].value == "Kick list"


# --- cost -------------------------------------------------------------------------


def test_the_query_count_does_not_grow_with_the_rows(signed_in, teams):
    def count_for(total):
        Player.objects.all().delete()
        for index in range(total):
            make("P", f"Q{index:02}", fp=20 + index)
        with CaptureQueriesContext(connection) as queries:
            assert len(names(export(signed_in))) == total
        return len(queries)

    assert count_for(3) == count_for(12)


# --- the buttons ------------------------------------------------------------------


def test_the_player_list_has_the_download_buttons(signed_in, players):
    body = signed_in.get(
        reverse("nba:player-list"), {"position": "G", "page": "1"}
    ).content.decode()

    href = f'href="{reverse("nba:player-export")}?position=G"'
    assert body.count(href) == 2  # phone and wider screens
    assert "Download as Excel" in body


def test_the_watchlist_buttons_export_the_watchlist(signed_in, players, user):
    WatchlistEntry.objects.create(
        user=user, player=players["Tatum"], stance=WatchlistEntry.Stance.WATCH
    )

    body = signed_in.get(reverse("nba:watchlist")).content.decode()

    assert f'data-export-url="{reverse("nba:watchlist-export")}"' in body


# --- Latest FP --------------------------------------------------------------------


def test_latest_fp_and_its_change_get_columns_of_their_own(signed_in, teams):
    import datetime as dt

    from django.utils import timezone

    from apps.fantasy import services
    from apps.fantasy.models import Season

    today = timezone.localdate()
    season = Season.objects.create(
        label="2026-27",
        starts_on=today - dt.timedelta(days=30),
        ends_on=today + dt.timedelta(days=150),
        is_current=True,
    )
    scorer = make("Big", "Scorer")
    start = timezone.now() - dt.timedelta(days=3)
    for index, (total_fp, games) in enumerate([(100, 4), (160, 5)]):
        services.record_snapshot(
            scorer,
            season,
            start + dt.timedelta(days=index),
            salary=1_000_000,
            total_fp=Decimal(total_fp),
            games_played=games,
            team=scorer.team,
            position=scorer.position,
        )

    row = table(export(signed_in, sort="latest"))[0][0]

    assert row["Latest FP"] == 60
    assert row["Change in points per game"] == pytest.approx(7.0)  # 25.0 -> 32.0
    assert row["Games"] == 5
