"""RotoWire player news: fetch the feed, match it to our players, keep every item.

The feed is the newest 25 NBA items and nothing older -- its `page` parameter
is accepted and ignored -- so an item missed between two pulls is gone. Each
pull is therefore stored in full, and an item already seen is updated in place
by its RotoWire ID rather than added again.
"""

import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import NbaApiUsage, Player, PlayerNews
from .services import DailyApiLimitExceeded, NbaApiError, normalize_name

logger = logging.getLogger(__name__)

PROVIDER = "rotowire"

# RotoWire's team codes where they are not ours. PHO is in the feed; the rest
# are the other spellings the same family of feeds is known to use.
FEED_TEAM_CODES = {
    "PHO": "PHX",
    "BRK": "BKN",
    "CHO": "CHA",
    "GS": "GSW",
    "NY": "NYK",
    "NO": "NOP",
    "SA": "SAS",
}

# ".../headlines/yaxel-lendeborg-injury-iffy-for-wednesday-536364": the
# player's name, the kind of item, the headline, and RotoWire's ID for it.
HEADLINE_URL = re.compile(r"/headlines/.+?-(?P<kind>injury|news)(?:-.*)?-(?P<id>\d+)/?$")

MONTHS = {
    month: number
    for number, month in enumerate(
        (
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ),
        start=1,
    )
}
FEED_DATE = re.compile(r"^(?P<month>[A-Za-z]+)\s+(?P<day>\d{1,2}),\s*(?P<year>\d{4})$")

# Dropped before names are compared: RotoWire writes "Jaren Jackson" where
# basketball.de writes "Jaren Jackson Jr.".
NAME_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv", "v"})


@dataclass(frozen=True)
class ParsedNews:
    rotowire_news_id: int
    kind: str
    headline: str
    body: str
    url: str
    published_on: date
    feed_player_id: int | None
    feed_player_name: str
    feed_team_abbreviation: str
    feed_position: str
    raw_payload: dict

    @property
    def team_code(self):
        return FEED_TEAM_CODES.get(self.feed_team_abbreviation, self.feed_team_abbreviation)


@dataclass(frozen=True)
class NewsSyncResult:
    items: int
    created: int
    updated: int
    matched: int
    unmatched: int
    relinked: int

    def __str__(self):
        return (
            f"{self.items} items, {self.created} new, {self.updated} already known; "
            f"{self.matched} matched, {self.unmatched} unmatched, "
            f"{self.relinked} older items linked."
        )


def fetch_news():
    """Fetch and decode the NBA news feed, consuming one of today's requests."""
    if not settings.RAPID_API_KEY:
        raise NbaApiError("RAPID_API_KEY is not configured.")

    _reserve_api_call()
    request = Request(
        settings.ROTOWIRE_NEWS_URL,
        headers={
            "X-RapidAPI-Key": settings.RAPID_API_KEY,
            "X-RapidAPI-Host": settings.ROTOWIRE_HOST,
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=settings.RAPID_API_TIMEOUT) as response:
            if response.status != 200:
                raise NbaApiError(f"RotoWire API returned HTTP {response.status}.")
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise NbaApiError(f"RotoWire API returned HTTP {exc.code}.") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise NbaApiError("RotoWire API request failed or returned invalid JSON.") from exc

    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise NbaApiError("RotoWire API returned an unexpected response.")
    return payload


@transaction.atomic
def _reserve_api_call(now=None):
    """Count one request against today's RotoWire tally, or refuse it."""
    now = now or timezone.now()
    usage, _ = NbaApiUsage.objects.get_or_create(
        provider=PROVIDER,
        usage_date=timezone.localtime(now).date(),
        defaults={"request_count": 0},
    )
    usage = NbaApiUsage.objects.select_for_update().get(pk=usage.pk)
    if usage.request_count >= settings.ROTOWIRE_DAILY_LIMIT:
        raise DailyApiLimitExceeded(
            f"The {PROVIDER} daily limit of {settings.ROTOWIRE_DAILY_LIMIT} requests "
            "has been reached."
        )
    usage.request_count += 1
    usage.save(update_fields=["request_count", "updated_at"])


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_feed_date(value):
    """`date` for the feed's "October 27, 2026", or None.

    Read by hand rather than with `strptime("%B")`, whose month names follow
    the process locale.
    """
    match = FEED_DATE.match(_text(value))
    month = MONTHS.get(match["month"].casefold()) if match else None
    if not month:
        return None
    try:
        return date(int(match["year"]), month, int(match["day"]))
    except ValueError:
        return None


def parse_news(payload):
    """The usable items of a feed response, in feed order.

    An item without an ID in its article link is skipped: inventing a key for
    it would store it again on the next pull.
    """
    parsed = []
    for record in payload.get("items") or []:
        if not isinstance(record, dict):
            continue
        url = _text(record.get("headlineUrl"))
        match = HEADLINE_URL.search(url)
        if not match:
            logger.warning("RotoWire item skipped, no ID in its link: %r", url)
            continue
        published_on = parse_feed_date(record.get("timestamp")) or timezone.localdate()
        parsed.append(
            ParsedNews(
                rotowire_news_id=int(match["id"]),
                kind=match["kind"],
                headline=_text(record.get("headline"))[:255],
                body=_text(record.get("news")),
                url=url,
                published_on=published_on,
                feed_player_id=_int(record.get("playerId")),
                feed_player_name=_text(record.get("player"))[:160],
                # Not `team`: the feed glues the injury onto the team's name.
                feed_team_abbreviation=_text(record.get("teamAbbr")).upper()[:8],
                feed_position=_text(record.get("position"))[:8],
                raw_payload=record,
            )
        )
    return parsed


def name_key(*parts):
    """A player's name reduced to what two sources agree on.

    Accents, case, punctuation and generational suffixes go: "A.J. Green" and
    "AJ Green" are one key, as are "Jaren Jackson Jr." and "Jaren Jackson".
    """
    words = re.sub(r"[.'\u2019]", "", normalize_name(" ".join(parts)))
    words = re.sub(r"[^\w]+", " ", words).split()
    return " ".join(word for word in words if word not in NAME_SUFFIXES)


class PlayerMatcher:
    """Finds our player for a news item, and remembers RotoWire's ID for him.

    In order: the RotoWire ID stored by an earlier match; then name and team;
    then the name alone, for a player the feed still lists with his old team.
    A name that fits two players links neither -- no link beats a wrong one.
    """

    def __init__(self):
        players = list(Player.objects.select_related("team"))
        self.by_rotowire_id = {p.rotowire_id: p for p in players if p.rotowire_id is not None}
        self.by_name = {}
        for player in players:
            if player.is_active:
                key = name_key(player.first_name, player.last_name)
                self.by_name.setdefault(key, []).append(player)

    def match(self, item):
        if item.feed_player_id is not None and item.feed_player_id in self.by_rotowire_id:
            return self.by_rotowire_id[item.feed_player_id]

        # A player who already carries another RotoWire ID is somebody else
        # with the same name.
        candidates = [
            player
            for player in self.by_name.get(name_key(item.feed_player_name), [])
            if player.rotowire_id is None
        ]
        same_team = [
            player
            for player in candidates
            if player.team and player.team.abbreviation == item.team_code
        ]
        for found in (same_team, candidates):
            if len(found) == 1:
                return self._remember(found[0], item.feed_player_id)
        return None

    def _remember(self, player, rotowire_id):
        if rotowire_id is not None:
            player.rotowire_id = rotowire_id
            player.save(update_fields=["rotowire_id", "updated_at"])
            self.by_rotowire_id[rotowire_id] = player
        return player


def _parsed_from_row(row):
    """A stored item as the matcher reads it, for retrying an unmatched one."""
    return ParsedNews(
        rotowire_news_id=row.rotowire_news_id,
        kind=row.kind,
        headline=row.headline,
        body=row.body,
        url=row.url,
        published_on=row.published_on,
        feed_player_id=row.feed_player_id,
        feed_player_name=row.feed_player_name,
        feed_team_abbreviation=row.feed_team_abbreviation,
        feed_position=row.feed_position,
        raw_payload=row.raw_payload,
    )


@transaction.atomic
def sync_news(payload=None):
    """Store one pull of the feed; fetch it first when no payload is given."""
    if payload is None:
        payload = fetch_news()
    items = parse_news(payload)
    matcher = PlayerMatcher()

    created = matched = 0
    for item in items:
        player = matcher.match(item)
        defaults = {
            "kind": item.kind,
            "headline": item.headline,
            "body": item.body,
            "url": item.url,
            "published_on": item.published_on,
            "feed_player_id": item.feed_player_id,
            "feed_player_name": item.feed_player_name,
            "feed_team_abbreviation": item.feed_team_abbreviation,
            "feed_position": item.feed_position,
            "raw_payload": item.raw_payload,
        }
        if player is not None:
            # Never cleared here: a link set by hand in the admin survives a
            # pull that cannot match the name.
            defaults["player"] = player
        row, was_created = PlayerNews.objects.update_or_create(
            rotowire_news_id=item.rotowire_news_id, defaults=defaults
        )
        created += was_created
        matched += row.player_id is not None

    # Items stored before their player was: the basketball.de import may have
    # added him since, or another item may have taught us his RotoWire ID.
    seen = {item.rotowire_news_id for item in items}
    relinked = 0
    for row in PlayerNews.objects.filter(player__isnull=True).exclude(rotowire_news_id__in=seen):
        player = matcher.match(_parsed_from_row(row))
        if player is not None:
            row.player = player
            row.save(update_fields=["player", "updated_at"])
            relinked += 1

    return NewsSyncResult(
        items=len(items),
        created=created,
        updated=len(items) - created,
        matched=matched,
        unmatched=len(items) - matched,
        relinked=relinked,
    )
