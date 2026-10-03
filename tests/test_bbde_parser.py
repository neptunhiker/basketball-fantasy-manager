"""Reading basketball.de's player list, against pages saved from the live site.

The three fixtures are the first, second and last page of the list as a
logged-in player sees it (October 2026, before tip-off, so every point column
is still 0.00). Nothing here touches the network.
"""

import io
import json
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs

import pytest

from apps.nba.bbde import (
    LIST_URL,
    LOGIN_URL,
    BbdeAccountNotActivated,
    BbdeClient,
    BbdeError,
    BbdeLayoutChanged,
    BbdeLoginError,
    BbdeSessionExpired,
    derive_games_played,
    parse_page,
)

FIXTURES = Path(__file__).parent / "fixtures" / "bbde"


def page(name):
    return (FIXTURES / f"page_{name}.html").read_text(encoding="utf-8")


def by_name(parsed, last_name, first_name):
    return next(
        row for row in parsed.rows if row.last_name == last_name and row.first_name == first_name
    )


# --- the saved pages ----------------------------------------------------------


@pytest.mark.parametrize(("name", "count"), [("first", 25), ("middle", 25), ("last", 12)])
def test_every_player_row_is_read(name, count):
    parsed = parse_page(page(name))

    assert len(parsed.rows) == count
    assert parsed.problems == []


def test_a_row_carries_id_team_position_and_money():
    row = by_name(parse_page(page("first")), "Adebayo", "Bam")

    assert row.bbde_id == 1153
    assert row.team_name == "Miami Heat"
    assert row.position == "C"
    # The list prints millions; the app stores dollars.
    assert row.salary == Decimal("10170000.00")
    assert row.total_fp == Decimal("0.00")
    assert row.fp_per_game == Decimal("0.00")
    assert row.games_played == 0
    assert row.games_consistent


def test_rookies_are_flagged():
    parsed = parse_page(page("first"))

    rookies = {row.display_name for row in parsed.rows if row.is_rookie}
    assert rookies == {"Darius Acuff Jr.", "Nate Ament", "Christian Anderson"}


def test_name_suffixes_and_hyphens_stay_in_the_last_name():
    parsed = parse_page(page("first"))
    middle = parse_page(page("middle"))

    assert by_name(parsed, "Acuff Jr.", "Darius")
    assert by_name(parsed, "Alexander-Walker", "Nickeil")
    assert by_name(middle, "Bagley III", "Marvin").bbde_id == 3489


def test_trailing_spaces_in_names_are_trimmed():
    # The site prints "Achiuwa, Precious " with a trailing space.
    row = parse_page(page("first")).rows[0]

    assert (row.first_name, row.last_name) == ("Precious", "Achiuwa")


def test_free_agents_keep_the_literal_team_label():
    row = by_name(parse_page(page("first")), "Antetokounmpo", "Alex")

    assert row.team_name == "Free Agents"


def test_the_pager_points_onwards_until_the_last_page():
    first = parse_page(page("first"))
    middle = parse_page(page("middle"))
    last = parse_page(page("last"))

    assert first.next_url == f"{LIST_URL}?p=2"
    assert first.last_page == 31
    assert middle.next_url == f"{LIST_URL}?p=3"
    assert last.next_url is None


# --- pages that are not the list ------------------------------------------------


def test_the_login_screen_is_recognised():
    html = '<div class="splash"><a href="/app/auth/login" class="lnkLogin">Login</a></div>'

    with pytest.raises(BbdeSessionExpired):
        parse_page(html)


def test_a_missing_column_is_a_layout_change_not_a_guess():
    html = page("last").replace('rel="salary"', 'rel="price"')

    with pytest.raises(BbdeLayoutChanged, match="salary"):
        parse_page(html)


def test_a_page_without_the_table_is_a_layout_change():
    with pytest.raises(BbdeLayoutChanged):
        parse_page("<html><body><p>Wartungsarbeiten</p></body></html>")


def test_one_unreadable_row_is_reported_and_the_rest_are_kept():
    html = page("last").replace('<td class="center">C</td>', '<td class="center">X</td>', 1)

    parsed = parse_page(html)

    assert len(parsed.rows) == 11
    assert parsed.problems == [("Wiseman, James", "unknown position 'X'")]


def test_thousands_separators_are_read():
    html = page("last").replace(
        '<td class="center">7.46</td>\n\t\t\t\t\t\t\t<td class="center">0.00</td>\n'
        '\t\t\t\t\t\t\t<td class="center">0.00</td>',
        '<td class="center">7.46</td>\n\t\t\t\t\t\t\t<td class="center">1,307.00</td>\n'
        '\t\t\t\t\t\t\t<td class="center">36.31</td>',
    )

    row = by_name(parse_page(html), "Zubac", "Ivica")

    assert row.total_fp == Decimal("1307.00")
    assert row.games_played == 36


# --- games played ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("total", "per_game", "games"),
    [
        # Giannis Antetokounmpo's 2025-26 line from his profile: G = 36.
        ("1307.00", "36.31", 36),
        ("2919.00", "42.93", 68),
        ("79.50", "26.50", 3),
        ("0.00", "0.00", 0),
        # A full season at a low rate still divides back exactly.
        ("82.00", "1.00", 82),
        ("-3.00", "-1.50", 2),
    ],
)
def test_games_played_is_recovered_from_the_point_columns(total, per_game, games):
    assert derive_games_played(Decimal(total), Decimal(per_game)) == (games, True)


def test_disagreeing_point_columns_are_flagged():
    games, consistent = derive_games_played(Decimal("10.00"), Decimal("0.00"))

    assert games == 0
    assert not consistent


# --- the client -----------------------------------------------------------------


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """Answers requests from a dict of URL -> body, and remembers them."""

    def __init__(self, responses):
        self.responses = responses
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        body = self.responses[request.full_url]
        if isinstance(body, Exception):
            raise body
        return FakeResponse(body.encode("utf-8"))


def login_answer(**fields):
    return json.dumps({"status": False, "url": "/app/", "notactivated": False, **fields})


def test_login_posts_the_form_the_site_expects():
    opener = FakeOpener({LOGIN_URL: login_answer(status=True)})

    BbdeClient(opener=opener).login("coach", "s3cret")

    request = opener.requests[0]
    assert request.get_method() == "POST"
    assert parse_qs(request.data.decode()) == {"username": ["coach"], "password": ["s3cret"]}


def test_a_rejected_login_says_so_without_repeating_the_password():
    opener = FakeOpener({LOGIN_URL: login_answer()})

    with pytest.raises(BbdeLoginError) as caught:
        BbdeClient(opener=opener).login("coach", "s3cret")

    assert "s3cret" not in str(caught.value)


def test_an_unconfirmed_account_is_told_apart():
    opener = FakeOpener({LOGIN_URL: login_answer(notactivated=True)})

    with pytest.raises(BbdeAccountNotActivated):
        BbdeClient(opener=opener).login("coach", "s3cret")


def test_an_http_error_becomes_a_readable_error():
    opener = FakeOpener({LOGIN_URL: HTTPError(LOGIN_URL, 503, "busy", {}, None)})

    with pytest.raises(BbdeError, match="HTTP 503"):
        BbdeClient(opener=opener).login("coach", "s3cret")


def test_iter_pages_follows_the_pager_and_pauses_between_pages():
    second = page("middle").replace("?p=3", "?p=99")  # make page 2 point at "the end"
    last = page("last")
    opener = FakeOpener(
        {LIST_URL: page("first"), f"{LIST_URL}?p=2": second, f"{LIST_URL}?p=99": last}
    )
    pauses = []

    pages = list(BbdeClient(opener=opener, sleep=pauses.append).iter_pages())

    assert [len(p.rows) for p in pages] == [25, 25, 12]
    assert len(pauses) == 2


def test_iter_pages_stops_at_a_pager_that_loops():
    html = page("first").replace(f"{LIST_URL}?p=2", LIST_URL)
    opener = FakeOpener({LIST_URL: html})

    with pytest.raises(BbdeLayoutChanged, match="already read"):
        list(BbdeClient(opener=opener, sleep=lambda _: None).iter_pages())


def test_iter_pages_never_follows_the_pager_off_site():
    html = page("first").replace(f"{LIST_URL}?p=2", "https://example.com/?p=2")
    opener = FakeOpener({LIST_URL: html})

    with pytest.raises(BbdeLayoutChanged, match="another site"):
        list(BbdeClient(opener=opener, sleep=lambda _: None).iter_pages())

    assert len(opener.requests) == 1
