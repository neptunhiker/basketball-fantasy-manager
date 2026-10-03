"""Reading the player list of basketball.de's US-Manager.

Two halves that never meet the database: a small client that logs in and walks
the paginated list, and a parser that turns one page of HTML into rows. Kept
apart so the parser can be tested against saved pages without a network or an
account, and so nothing in here can ever write a credential anywhere.

The list lives at /app/usmanager/top-spieler/?p=N and is only shown to
logged-in players of the game. One page is one server-rendered table of 25
players; there is no JSON endpoint behind it.
"""

import json
import re
import time
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener

from bs4 import BeautifulSoup

BASE_URL = "https://basketball.de"
LOGIN_URL = f"{BASE_URL}/app/auth/logon"
LIST_URL = f"{BASE_URL}/app/usmanager/top-spieler/"
USER_AGENT = "Mozilla/5.0 (compatible; CrossoverManager player import)"

# 31 pages today. The cap only exists so a pager that links back to itself can
# never keep a request running forever.
MAX_PAGES = 100
REQUEST_DELAY_SECONDS = 0.5
TIMEOUT_SECONDS = 20

MILLION = Decimal("1000000")
CENT = Decimal("0.01")
POSITIONS = {"G", "F", "C"}

# The header links carry the sort key of their column in `rel`. Matching on
# those instead of on column order means an added or reordered column cannot
# shift salaries into the points column without anybody noticing.
REQUIRED_COLUMNS = ("lastName", "team", "isGerman", "position", "salary", "FP", "avgFP")
PROFILE_ID = re.compile(r"/players/profile/(\d+)")


class BbdeError(RuntimeError):
    """basketball.de could not be read. The message is safe to show to staff."""


class BbdeLoginError(BbdeError):
    """basketball.de turned the login down."""


class BbdeAccountNotActivated(BbdeLoginError):
    """The account exists but its confirmation link was never clicked."""


class BbdeSessionExpired(BbdeError):
    """A page came back as the login screen instead of the player list."""


class BbdeLayoutChanged(BbdeError):
    """The page no longer looks the way the parser expects."""


@dataclass(frozen=True)
class BbdeRow:
    """One player as the official game lists them."""

    bbde_id: int | None
    first_name: str
    last_name: str
    team_name: str
    is_rookie: bool
    position: str
    salary: Decimal  # in dollars, like PlayerSnapshot.salary
    total_fp: Decimal
    fp_per_game: Decimal
    games_played: int
    # False when the derived games played does not reproduce FP/G. Kept rather
    # than dropped, because the salary and points are still right.
    games_consistent: bool

    @property
    def display_name(self):
        return f"{self.first_name} {self.last_name}".strip()


@dataclass
class BbdePage:
    rows: list[BbdeRow] = field(default_factory=list)
    # Rows the parser could not read, as (name, reason) for the import summary.
    problems: list[tuple[str, str]] = field(default_factory=list)
    next_url: str | None = None
    last_page: int | None = None


# --- parsing -----------------------------------------------------------------


def derive_games_played(total_fp, fp_per_game):
    """Games played, recovered from the two point columns the list shows.

    The list has no games column, but FP/G is FP divided by games and rounded to
    two places. Dividing back and rounding to a whole number is exact for any
    realistic player: the rounding error of FP/G is at most 0.005, which over a
    full 82-game season moves FP by under half a point.

    Returns (games, consistent). `consistent` is False when no whole number of
    games reproduces the published FP/G -- a sign the two columns disagree.
    """
    if fp_per_game == 0:
        return 0, total_fp == 0
    games = int((total_fp / fp_per_game).to_integral_value(rounding=ROUND_HALF_UP))
    if games <= 0:
        return 0, False
    tolerance = Decimal("0.005") * games + CENT
    return games, abs(fp_per_game * games - total_fp) <= tolerance


def _clean(text):
    return " ".join((text or "").split())


def _decimal(text):
    # Large totals are printed with a thousands separator ("1,307.00").
    try:
        return Decimal(_clean(text).replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError(f"not a number: {text!r}") from exc


def _split_name(text):
    """'Bagley III, Marvin' -> ('Marvin', 'Bagley III')."""
    last, _, first = _clean(text).partition(",")
    return first.strip(), last.strip()


def _is_login_page(soup):
    return soup.select_one("a.lnkLogin") is not None or soup.select_one("#frmLogin") is not None


def _column_indexes(header):
    indexes = {}
    for index, cell in enumerate(header.find_all(["th", "td"])):
        link = cell.find("a", class_="lnkSort")
        if link is None:
            continue
        # BeautifulSoup treats `rel` as a multi-valued attribute.
        rel = link.get("rel") or []
        key = " ".join(rel) if isinstance(rel, list) else rel
        indexes.setdefault(key, index)
    missing = [column for column in REQUIRED_COLUMNS if column not in indexes]
    if missing:
        raise BbdeLayoutChanged(
            "The basketball.de player list has changed: missing column(s) "
            + ", ".join(missing)
            + "."
        )
    return indexes


def _parse_row(cells, columns):
    name_cell = cells[columns["lastName"]]
    first_name, last_name = _split_name(name_cell.get_text())
    if not last_name:
        raise ValueError("no name")

    link = name_cell.find("a", href=True)
    match = PROFILE_ID.search(link["href"]) if link else None
    bbde_id = int(match.group(1)) if match else None

    position = _clean(cells[columns["position"]].get_text()).upper()
    if position not in POSITIONS:
        raise ValueError(f"unknown position {position!r}")

    rookie_cell = cells[columns["isGerman"]]
    is_rookie = rookie_cell.get("data-compare") == "1" or rookie_cell.find("img") is not None

    salary = (_decimal(cells[columns["salary"]].get_text()) * MILLION).quantize(CENT)
    total_fp = _decimal(cells[columns["FP"]].get_text())
    fp_per_game = _decimal(cells[columns["avgFP"]].get_text())
    games_played, consistent = derive_games_played(total_fp, fp_per_game)

    return BbdeRow(
        bbde_id=bbde_id,
        first_name=first_name,
        last_name=last_name,
        team_name=_clean(cells[columns["team"]].get_text()),
        is_rookie=is_rookie,
        position=position,
        salary=salary,
        total_fp=total_fp,
        fp_per_game=fp_per_game,
        games_played=games_played,
        games_consistent=consistent,
    )


def _pager(soup):
    """The 'next' link and the number of the last page, from the pager."""
    next_url = None
    last_page = None
    pager = soup.select_one("div.pager")
    for link in pager.find_all("a", href=True) if pager else []:
        label = _clean(link.get_text())
        href = link["href"]
        if label.startswith("Nächste"):
            next_url = href
        elif label.startswith("Letzte"):
            numbers = parse_qs(urlsplit(href).query).get("p")
            if numbers and numbers[0].isdigit():
                last_page = int(numbers[0])
    return next_url, last_page


def parse_page(html):
    """Read one page of the player list.

    Raises BbdeSessionExpired for the login screen and BbdeLayoutChanged when
    the table cannot be found or a column is missing. A single unreadable row
    does not fail the page; it is reported in `problems` instead.
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("div.sc-list table")
    if table is None:
        if _is_login_page(soup):
            raise BbdeSessionExpired(
                "basketball.de showed the login page instead of the player list."
            )
        raise BbdeLayoutChanged("The basketball.de player list has changed: no player table.")

    rows = table.find_all("tr")
    header = next((row for row in rows if row.find("th")), None)
    if header is None:
        raise BbdeLayoutChanged("The basketball.de player list has changed: no table header.")
    columns = _column_indexes(header)
    width = max(columns.values()) + 1

    page = BbdePage()
    for row in rows:
        if row is header:
            continue
        cells = row.find_all("td", recursive=False)
        if not cells:
            continue
        if len(cells) < width:
            page.problems.append((_clean(row.get_text())[:60], "too few columns"))
            continue
        try:
            page.rows.append(_parse_row(cells, columns))
        except ValueError as exc:
            page.problems.append((_clean(cells[columns["lastName"]].get_text()), str(exc)))

    page.next_url, page.last_page = _pager(soup)
    return page


# --- fetching ----------------------------------------------------------------


class BbdeClient:
    """A logged-in session with basketball.de, held in memory only.

    The session cookie lives in this object's cookie jar and disappears with
    it. Nothing here logs request bodies, and the password is never stored on
    the instance.
    """

    def __init__(self, *, opener=None, delay=REQUEST_DELAY_SECONDS, sleep=time.sleep):
        self._opener = opener or build_opener(HTTPCookieProcessor(CookieJar()))
        self._delay = delay
        self._sleep = sleep

    def _open(self, request):
        request.add_header("User-Agent", USER_AGENT)
        try:
            with self._opener.open(request, timeout=TIMEOUT_SECONDS) as response:
                return response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            raise BbdeError(f"basketball.de returned HTTP {exc.code}.") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise BbdeError("basketball.de could not be reached.") from exc

    def login(self, username, password):
        """Log in the way the site's own login form does (an AJAX POST)."""
        body = urlencode({"username": username, "password": password}).encode()
        request = Request(
            LOGIN_URL,
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json, text/javascript, */*",
            },
            method="POST",
        )
        text = self._open(request)
        try:
            answer = json.loads(text)
        except json.JSONDecodeError as exc:
            raise BbdeError("basketball.de gave an unexpected answer to the login.") from exc
        if not isinstance(answer, dict) or not answer.get("status"):
            if isinstance(answer, dict) and answer.get("notactivated"):
                raise BbdeAccountNotActivated(
                    "This basketball.de account has not been activated yet."
                )
            raise BbdeLoginError("basketball.de did not accept this username and password.")

    def fetch(self, url):
        return self._open(Request(url, headers={"Accept": "text/html"}))

    def iter_pages(self):
        """Yield every page of the player list, following the 'next' link.

        Never leaves basketball.de: the session cookie must not be sent to a
        host the pager might point at.
        """
        url = LIST_URL
        seen = set()
        while url:
            if url in seen:
                raise BbdeLayoutChanged(
                    "The basketball.de pager links back to a page already read."
                )
            if len(seen) >= MAX_PAGES:
                raise BbdeError(f"The basketball.de player list has more than {MAX_PAGES} pages.")
            if seen:
                self._sleep(self._delay)
            seen.add(url)
            page = parse_page(self.fetch(url))
            yield page
            url = page.next_url
            if url and not url.startswith(f"{BASE_URL}/"):
                raise BbdeLayoutChanged("The basketball.de pager points to another site.")
