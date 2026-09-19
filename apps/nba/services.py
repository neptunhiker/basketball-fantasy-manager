"""Provider integration and persistence for NBA injury data."""

import json
import logging
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from .models import NbaApiUsage, Player, PlayerInjury

logger = logging.getLogger(__name__)

PROVIDER = "api-basketball-nba"


class NbaApiError(RuntimeError):
    """The provider could not return a usable response."""


class DailyApiLimitExceeded(NbaApiError):
    """The configured daily outbound-request limit has been reached."""


@dataclass(frozen=True)
class ParsedInjury:
    provider_injury_id: str
    provider_source_id: str
    provider_source_state: str
    provider_source_description: str
    first_name: str
    last_name: str
    feed_athlete_id: str
    feed_player_position: str
    feed_player_position_abbreviation: str
    feed_player_status: str
    team_abbreviation: str
    status: str
    injury_status_type: str
    fantasy_status: str
    fantasy_status_abbreviation: str
    reported_at: datetime | None
    return_date: date | None
    injury_type: str
    injury_location: str
    injury_detail: str
    injury_side: str
    short_comment: str
    long_comment: str
    headline: str
    headline_source: str
    feed_player_name: str
    feed_team_id: str
    feed_team_name: str
    raw_payload: dict


def fetch_injuries():
    """Fetch and decode the provider response, consuming one daily slot."""
    if not settings.RAPID_API_KEY:
        raise NbaApiError("RAPID_API_KEY is not configured.")

    _reserve_api_call()
    request = Request(
        settings.RAPID_API_INJURIES_URL,
        headers={
            "X-RapidAPI-Key": settings.RAPID_API_KEY,
            "X-RapidAPI-Host": settings.RAPID_API_HOST,
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=settings.RAPID_API_TIMEOUT) as response:
            if response.status != 200:
                raise NbaApiError(f"NBA API returned HTTP {response.status}.")
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise NbaApiError(f"NBA API returned HTTP {exc.code}.") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise NbaApiError("NBA API request failed or returned invalid JSON.") from exc

    if not isinstance(payload, dict) or payload.get("status") not in {None, "success"}:
        raise NbaApiError("NBA API returned an unsuccessful response.")
    return payload


@transaction.atomic
def _reserve_api_call(now=None):
    now = now or timezone.now()
    usage, _ = NbaApiUsage.objects.get_or_create(
        provider=PROVIDER,
        usage_date=timezone.localtime(now).date(),
        defaults={"request_count": 0},
    )
    usage = NbaApiUsage.objects.select_for_update().get(pk=usage.pk)
    daily_limit = min(settings.RAPID_API_DAILY_LIMIT, 1)
    if usage.request_count >= daily_limit:
        raise DailyApiLimitExceeded(
            f"The {PROVIDER} daily limit of {daily_limit} "
            "requests has been reached."
        )
    usage.request_count += 1
    usage.save(update_fields=["request_count", "updated_at"])


def normalize_name(value):
    """Make provider and local names comparable without changing stored names."""
    normalized = unicodedata.normalize("NFKD", value or "")
    return "".join(
        character for character in normalized if not unicodedata.combining(character)
    ).casefold()


def _text(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in (
            "displayName",
            "name",
            "short",
            "value",
            "text",
            "status",
            "abbreviation",
            "description",
        ):
            if value.get(key):
                return str(value[key]).strip()
    return ""


def _date_value(value):
    if not value:
        return None
    return parse_date(str(value))


def _datetime_value(value):
    if not value:
        return None
    parsed = parse_datetime(str(value))
    if parsed is None:
        parsed_date = parse_date(str(value))
        return timezone.make_aware(datetime.combine(parsed_date, time.min)) if parsed_date else None
    return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed


def _iter_records(payload):
    for group in payload.get("response") or payload.get("injuries") or []:
        if not isinstance(group, dict):
            continue
        group_team = group.get("team") or {}
        for record in group.get("injuries", []):
            if not isinstance(record, dict):
                continue
            athlete = record.get("athlete") or {}
            team = athlete.get("team") or group_team
            yield record, athlete, team


def parse_injuries(payload):
    """Flatten the provider's grouped, nested injury response."""
    parsed = []
    for record, athlete, team in _iter_records(payload):
        details = record.get("details") or {}
        injury_type = details.get("type") or record.get("type") or record.get("injury") or {}
        source = record.get("source") or {}
        note_items = (athlete.get("notes") or {}).get("items") or []
        note = note_items[0] if note_items else {}
        fantasy_status = details.get("fantasyStatus") or record.get("fantasyStatus")
        position = athlete.get("position") or {}
        athlete_status = athlete.get("status") or {}
        first_name = _text(athlete.get("firstName"))
        last_name = _text(athlete.get("lastName"))
        parsed.append(
            ParsedInjury(
                provider_injury_id=str(record.get("id") or ""),
                provider_source_id=str(source.get("id") or ""),
                provider_source_state=_text(source.get("state")),
                provider_source_description=_text(source.get("description")),
                first_name=first_name,
                last_name=last_name,
                feed_athlete_id=str(athlete.get("id") or ""),
                feed_player_position=_text(position.get("displayName") or position.get("name")),
                feed_player_position_abbreviation=_text(position.get("abbreviation")),
                feed_player_status=_text(athlete_status),
                team_abbreviation=_text(team.get("abbreviation")).upper(),
                status=_text(record.get("status")),
                injury_status_type=_text(
                    (record.get("type") or {}).get("name")
                    if isinstance(record.get("type"), dict)
                    else record.get("type")
                ),
                fantasy_status=_text(fantasy_status),
                fantasy_status_abbreviation=_text(
                    record.get("fantasyStatusAbbreviation")
                    or (
                        fantasy_status.get("abbreviation")
                        if isinstance(fantasy_status, dict)
                        else ""
                    )
                ),
                reported_at=_datetime_value(record.get("reportedDate") or record.get("date")),
                return_date=_date_value(details.get("returnDate") or record.get("returnDate")),
                injury_type=_text(
                    injury_type.get("displayName") or injury_type.get("name")
                    if isinstance(injury_type, dict)
                    else injury_type
                ),
                injury_location=_text(details.get("location") or record.get("location")),
                injury_detail=_text(details.get("detail") or record.get("detail")),
                injury_side=_text(details.get("side") or record.get("side")),
                short_comment=_text(record.get("shortComment")),
                long_comment=_text(record.get("longComment")),
                headline=_text(note.get("headline")),
                headline_source=_text(note.get("source")),
                feed_player_name=_text(athlete.get("displayName")) or " ".join(
                    part for part in (first_name, last_name) if part
                ),
                feed_team_id=str(team.get("id") or ""),
                feed_team_name=_text(team.get("name")),
                raw_payload=record,
            )
        )
    return parsed


def _match_players(injuries):
    players = list(Player.objects.select_related("team").filter(is_active=True))
    matches = []
    for injury in injuries:
        candidates = [
            player
            for player in players
            if player.team
            and player.team.abbreviation == injury.team_abbreviation
            and normalize_name(player.first_name) == normalize_name(injury.first_name)
            and normalize_name(player.last_name) == normalize_name(injury.last_name)
        ]
        matches.append((injury, candidates))
    return matches


@transaction.atomic
def sync_injuries(payload=None, observed_at=None):
    """Fetch, match and persist injury records; return an auditable summary."""
    observed_at = observed_at or timezone.now()
    payload = fetch_injuries() if payload is None else payload
    summary = {"created": 0, "updated": 0, "unmatched": 0, "ambiguous": 0}

    for injury, candidates in _match_players(parse_injuries(payload)):
        if not candidates:
            summary["unmatched"] += 1
            logger.warning(
                "Unmatched NBA injury: %s %s (%s)",
                injury.first_name,
                injury.last_name,
                injury.team_abbreviation,
            )
            continue
        if len(candidates) > 1:
            summary["ambiguous"] += 1
            logger.warning(
                "Ambiguous NBA injury: %s %s (%s)",
                injury.first_name,
                injury.last_name,
                injury.team_abbreviation,
            )
            continue

        defaults = {
            "player": candidates[0],
            "provider_source_id": injury.provider_source_id,
            "provider_source_state": injury.provider_source_state,
            "provider_source_description": injury.provider_source_description,
            "observed_at": observed_at,
            "reported_at": injury.reported_at,
            "status": injury.status,
            "injury_status_type": injury.injury_status_type,
            "fantasy_status": injury.fantasy_status,
            "fantasy_status_abbreviation": injury.fantasy_status_abbreviation,
            "return_date": injury.return_date,
            "injury_type": injury.injury_type,
            "injury_location": injury.injury_location,
            "injury_detail": injury.injury_detail,
            "injury_side": injury.injury_side,
            "short_comment": injury.short_comment,
            "long_comment": injury.long_comment,
            "headline": injury.headline,
            "headline_source": injury.headline_source,
            "feed_player_name": injury.feed_player_name,
            "feed_athlete_id": injury.feed_athlete_id,
            "feed_player_position": injury.feed_player_position,
            "feed_player_position_abbreviation": injury.feed_player_position_abbreviation,
            "feed_player_status": injury.feed_player_status,
            "feed_team_id": injury.feed_team_id,
            "feed_team_abbreviation": injury.team_abbreviation,
            "feed_team_name": injury.feed_team_name,
            "raw_payload": injury.raw_payload,
        }
        if injury.provider_injury_id:
            _, created = PlayerInjury.objects.get_or_create(
                provider=PROVIDER,
                provider_injury_id=injury.provider_injury_id,
                observed_at=observed_at,
                defaults=defaults,
            )
        else:
            PlayerInjury.objects.create(provider=PROVIDER, provider_injury_id="", **defaults)
            created = True
        summary["created" if created else "updated"] += 1
    return summary