"""Official season stats: reading the file and finding our player for each line.

The file is the per-game table NBA.com exports for a season, one line per
player. Parsing knows nothing about the database and returns plain rows;
matching turns each row into one of our players, or reports why it cannot.
Nothing is ever guessed: a name that fits two players is reported, not picked.
"""

import csv
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Max, OuterRef, Subquery
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import Player, PlayerSeasonStats, SeasonStatsImport
from .services import normalize_name

logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_ROWS = 1000

INTEGER = "integer"
PER_GAME = "per_game"
PERCENTAGE = "percentage"

# CSV header -> (model field, kind, upper bound). Columns are found by name, so
# the order in the file does not matter and anything not listed here (HSFP,
# DD2, TD3, +/- ...) is ignored.
COLUMNS = {
    "AGE": ("age", INTEGER, 50),
    "GP": ("games_played", INTEGER, 82),
    "W": ("wins", INTEGER, 82),
    "L": ("losses", INTEGER, 82),
    "MIN": ("minutes", PER_GAME, 48),
    "PTS": ("points", PER_GAME, 99),
    "FGM": ("field_goals_made", PER_GAME, 99),
    "FGA": ("field_goals_attempted", PER_GAME, 99),
    "FG%": ("field_goal_pct", PERCENTAGE, 100),
    "3PM": ("threes_made", PER_GAME, 99),
    "3PA": ("threes_attempted", PER_GAME, 99),
    "3P%": ("three_pct", PERCENTAGE, 100),
    "FTM": ("free_throws_made", PER_GAME, 99),
    "FTA": ("free_throws_attempted", PER_GAME, 99),
    "FT%": ("free_throw_pct", PERCENTAGE, 100),
    "OREB": ("offensive_rebounds", PER_GAME, 99),
    "DREB": ("defensive_rebounds", PER_GAME, 99),
    "REB": ("rebounds", PER_GAME, 99),
    "AST": ("assists", PER_GAME, 99),
    "TOV": ("turnovers", PER_GAME, 99),
    "STL": ("steals", PER_GAME, 99),
    "BLK": ("blocks", PER_GAME, 99),
    "PF": ("personal_fouls", PER_GAME, 99),
    "TFP": ("fantasy_points", PER_GAME, 199),
}
NAME_COLUMN = "PLAYER"
TEAM_COLUMN = "TEAM"
REQUIRED_COLUMNS = (NAME_COLUMN, TEAM_COLUMN, *COLUMNS)

STAT_FIELDS = tuple(model_field for model_field, _kind, _bound in COLUMNS.values())

# The file's spelling -> ours, for players the file calls by a nickname or
# writes family name first. Both sides go through `name_key`, so accents,
# case and suffixes do not matter here.
ALIASES = {
    "GG Jackson": "Gregory Jackson",
    "Nic Claxton": "Nicolas Claxton",
    "Bones Hyland": "Nah'Shon Hyland",
    "Ronald Holland II": "Ron Holland",
    "Yang Hansen": "Hansen Yang",
}

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
# Dots and apostrophes vanish ("A.J." is "AJ", "Nah'Shon" is "NahShon");
# apostrophes come straight, curly (U+2018, U+2019) or as a modifier letter.
_DROPPED = re.compile("[.'`\u2018\u2019\u02bc]")


class SeasonStatsFileError(ValueError):
    """The file as a whole cannot be read. The message is safe to show."""


class PreviewClosed(Exception):
    """The preview can no longer be confirmed or discarded. Safe to show."""


@dataclass(frozen=True)
class StatsRow:
    line: int
    name: str
    team: str
    values: dict

    @property
    def label(self):
        return f"{self.name} ({self.team})" if self.team else self.name


@dataclass
class ParsedFile:
    rows: list[StatsRow] = field(default_factory=list)
    # Lines that were left out, one readable sentence each.
    problems: list[str] = field(default_factory=list)


@dataclass
class MatchResult:
    matched: list[tuple[StatsRow, Player]] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)


def name_key(value):
    """A player's name reduced to what identifies them across sources.

    Builds on `normalize_name` (accents, case) and also drops punctuation and
    generational suffixes, which the official stats and basketball.de write
    differently: "Jaren Jackson Jr." and "Jaren Jackson" are the same key.
    """
    words = _DROPPED.sub("", normalize_name(value)).replace("-", " ").split()
    return " ".join(word for word in words if word not in _SUFFIXES)


_ALIAS_KEYS = {name_key(theirs): name_key(ours) for theirs, ours in ALIASES.items()}


def parse_season_stats(data):
    """Read a season stats file into rows.

    `data` is the raw bytes. Raises `SeasonStatsFileError` when the file as a
    whole is unusable (too large, not UTF-8, missing columns, too many rows).
    A single bad line is left out and described in `problems` instead, so one
    typo does not block a whole season.
    """
    if len(data) > MAX_FILE_BYTES:
        raise SeasonStatsFileError(_("The file is larger than 2 MB."))
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SeasonStatsFileError(
            _("The file is not UTF-8 encoded. Please export it again as “CSV UTF-8”.")
        ) from exc

    # Blank lines (or lines of bare separators) are skipped, but every line
    # keeps its number in the file, so a problem points at the right place.
    lines = [
        (number, line)
        for number, line in enumerate(text.splitlines(), start=1)
        if line.strip(" ;,\t")
    ]
    if not lines:
        raise SeasonStatsFileError(_("The file is empty."))
    delimiter = ";" if lines[0][1].count(";") >= lines[0][1].count(",") else ","

    def cells(line):
        return next(csv.reader([line], delimiter=delimiter))

    header = [cell.strip().upper() for cell in cells(lines[0][1])]
    missing = [column for column in REQUIRED_COLUMNS if column not in header]
    if missing:
        raise SeasonStatsFileError(
            _("These columns are missing: %(columns)s.") % {"columns": ", ".join(missing)}
        )
    position = {column: header.index(column) for column in REQUIRED_COLUMNS}

    records = [(number, cells(line)) for number, line in lines[1:]]
    if len(records) > MAX_ROWS:
        raise SeasonStatsFileError(
            _("The file has more than %(limit)d players.") % {"limit": MAX_ROWS}
        )

    # Semicolon files come from European spreadsheets, which may well write
    # "47,6"; in a comma-separated file the comma cannot be a decimal point.
    decimal_comma = delimiter == ";"
    parsed = ParsedFile()
    for number, record in records:
        row, problem = _parse_record(number, record, position, decimal_comma)
        if problem:
            parsed.problems.append(problem)
        else:
            parsed.rows.append(row)

    _drop_duplicates(parsed)
    return parsed


def _parse_record(line, record, position, decimal_comma):
    def cell(column):
        index = position[column]
        return record[index].strip() if index < len(record) else ""

    name = " ".join(cell(NAME_COLUMN).split())
    if not name:
        return None, _("Line %(line)d: the player name is empty.") % {"line": line}
    team = cell(TEAM_COLUMN).upper()

    values = {}
    for column, (model_field, kind, bound) in COLUMNS.items():
        raw = cell(column)
        value = _number(raw.replace(",", ".") if decimal_comma else raw)
        valid = (
            value is not None
            and 0 <= value <= bound
            and (kind != INTEGER or value == value.to_integral_value())
        )
        if not valid:
            return None, _("Line %(line)d (%(name)s): “%(value)s” is not a valid %(column)s.") % {
                "line": line,
                "name": name,
                "value": raw,
                "column": column,
            }
        values[model_field] = int(value) if kind == INTEGER else value.quantize(Decimal("0.1"))
    return StatsRow(line=line, name=name, team=team, values=values), None


def _number(raw):
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def _drop_duplicates(parsed):
    """Leave out every line whose player appears more than once.

    Two lines for one name mean the file is not what we think it is (a traded
    player split per team, say), and picking either would be a guess.
    """
    counts = {}
    for row in parsed.rows:
        counts[name_key(row.name)] = counts.get(name_key(row.name), 0) + 1
    kept = []
    for row in parsed.rows:
        if counts[name_key(row.name)] > 1:
            parsed.problems.append(
                _("Line %(line)d (%(name)s): the player appears more than once in the file.")
                % {"line": row.line, "name": row.name}
            )
        else:
            kept.append(row)
    parsed.rows = kept


def match_rows(rows, players=None):
    """Find our player for each row.

    Compares names through `name_key` and the alias list, against active and
    inactive players alike. The team in the file is last season's, so it only
    breaks a tie between players of the same name; it never rules out a
    player who has moved since.
    """
    if players is None:
        players = Player.objects.select_related("team")
    by_key = {}
    for player in players:
        by_key.setdefault(name_key(player.full_name), []).append(player)

    result = MatchResult()
    found = []
    for row in rows:
        key = name_key(row.name)
        # The name as written wins; the alias only helps when it finds nobody,
        # so a later respelling on our side never makes a match worse.
        candidates = by_key.get(key) or by_key.get(_ALIAS_KEYS.get(key), [])
        if len(candidates) > 1:
            candidates = [
                player
                for player in candidates
                if player.team is not None and player.team.abbreviation == row.team
            ] or candidates
        if not candidates:
            result.unmatched.append(row.label)
        elif len(candidates) > 1:
            slugs = ", ".join(sorted(player.slug for player in candidates))
            result.ambiguous.append(f"{row.label}: {slugs}")
        else:
            found.append((row, candidates[0]))

    # Two lines landing on one player would overwrite each other silently.
    claims = {}
    for _row, player in found:
        claims[player.pk] = claims.get(player.pk, 0) + 1
    for row, player in found:
        if claims[player.pk] > 1:
            result.ambiguous.append(
                _("%(row)s: also matched by another line, %(player)s")
                % {"row": row.label, "player": player.slug}
            )
        else:
            result.matched.append((row, player))
    return result


# --- preview and import ---------------------------------------------------------

# How long a preview can be confirmed. Long enough to read it, short enough
# that the numbers it shows are still the numbers that get written.
PREVIEW_LIFETIME = timedelta(minutes=60)

_SEASON_LABEL = re.compile(r"^(\d{4})-(\d{2})$")


def validate_season_label(label):
    """`2025-26`: a start year and the last two digits of the year after it."""
    match = _SEASON_LABEL.match(label or "")
    if not match or (int(match[1]) + 1) % 100 != int(match[2]):
        raise SeasonStatsFileError(_("Use the format YYYY-YY, for example 2025-26."))


def expire_stale_previews():
    """Close previews nobody confirmed in time and drop their pending rows."""
    return SeasonStatsImport.objects.filter(
        status=SeasonStatsImport.Status.PREVIEWED,
        created_at__lt=timezone.now() - PREVIEW_LIFETIME,
    ).update(status=SeasonStatsImport.Status.EXPIRED, payload=[])


def preview_season_stats(
    data, *, season, file_name="", triggered_by=None, source=SeasonStatsImport.Source.WEB
):
    """Read and match a file and record what importing it would do.

    Writes no stats: the matched rows wait in the returned record's `payload`
    until `confirm_season_stats`. Raises `SeasonStatsFileError` -- and records
    nothing -- when the season label or the file as a whole is unusable.
    """
    validate_season_label(season)
    parsed = parse_season_stats(data)
    if not parsed.rows and not parsed.problems:
        raise SeasonStatsFileError(_("The file has no players."))
    matched = match_rows(parsed.rows)
    expire_stale_previews()

    existing = {
        stats.player_id: stats.player
        for stats in PlayerSeasonStats.objects.filter(season=season).select_related("player")
    }
    in_file = {player.pk for _row, player in matched.matched}
    leaving = sorted(
        (player for pk, player in existing.items() if pk not in in_file),
        key=lambda player: (player.last_name, player.first_name),
    )

    return SeasonStatsImport.objects.create(
        season=season,
        file_name=file_name[:255],
        triggered_by=triggered_by,
        source=source,
        rows=len(parsed.rows) + len(parsed.problems),
        matched=len(matched.matched),
        created=len(in_file - existing.keys()),
        updated=len(in_file & existing.keys()),
        removed=len(leaving),
        unmatched=matched.unmatched,
        ambiguous=matched.ambiguous,
        skipped=parsed.problems,
        warnings=[
            _("%(player)s is not in the file; their %(season)s stats will be removed.")
            % {"player": player.full_name, "season": season}
            for player in leaving
        ],
        payload=[_pending(row, player) for row, player in matched.matched],
    )


def _pending(row, player):
    # JSON has no decimals; the strings go back through Decimal on the way in.
    return {
        "player": str(player.pk),
        "name": row.name,
        "team": row.team,
        "values": {
            name: value if isinstance(value, int) else str(value)
            for name, value in row.values.items()
        },
    }


def confirm_season_stats(run, user=None):
    """Write a preview's rows, replacing everything stored for its season.

    All or nothing, in one transaction. Players in the season's stats but not
    in the file lose their row: the file is the whole season. Raises
    `PreviewClosed` when the preview was already used, discarded, expired or
    belongs to someone else.
    """
    try:
        with transaction.atomic():
            run = _open_preview(run, user)
            if run.status == SeasonStatsImport.Status.EXPIRED:
                closed = True
            elif not run.payload:
                # Importing nothing would only delete the season's stats.
                raise PreviewClosed(
                    _("No line of this file matched a player, so there is nothing to import.")
                )
            else:
                closed = False
                _write(run)
    except PreviewClosed:
        raise
    except Exception:
        logger.exception("Season stats import %s failed", run.pk)
        SeasonStatsImport.objects.filter(pk=run.pk).update(
            status=SeasonStatsImport.Status.FAILED,
            payload=[],
            error=_("Unexpected error. The details are in the server log."),
        )
        raise
    if closed:
        raise PreviewClosed(_("This preview has expired. Please upload the file again."))
    return run


def discard_season_stats(run, user=None):
    """Drop a preview without writing anything."""
    with transaction.atomic():
        run = _open_preview(run, user)
        if run.status == SeasonStatsImport.Status.PREVIEWED:
            run.status = SeasonStatsImport.Status.DISCARDED
            run.payload = []
            run.save(update_fields=["status", "payload", "updated_at"])
    return run


def _open_preview(run, user):
    """Lock the preview and check it may still be used.

    A preview past its lifetime is marked expired here and returned, so the
    caller can save that before saying no -- raising would roll it back.
    """
    run = SeasonStatsImport.objects.select_for_update().get(pk=run.pk)
    if user is not None and run.triggered_by_id != user.pk:
        raise PreviewClosed(_("Only the person who uploaded the file can import it."))
    if run.status != SeasonStatsImport.Status.PREVIEWED:
        raise PreviewClosed(_("This preview has already been closed."))
    if run.created_at < timezone.now() - PREVIEW_LIFETIME:
        run.status = SeasonStatsImport.Status.EXPIRED
        run.payload = []
        run.save(update_fields=["status", "payload", "updated_at"])
    return run


_INTEGER_FIELDS = {model_field for model_field, kind, _bound in COLUMNS.values() if kind == INTEGER}


def _write(run):
    now = timezone.now()
    players = Player.objects.in_bulk([item["player"] for item in run.payload])
    existing = {
        stats.player_id: stats
        for stats in PlayerSeasonStats.objects.select_for_update().filter(season=run.season)
    }

    new, changed, written = [], [], set()
    for item in run.payload:
        player = players.get(uuid.UUID(item["player"]))
        if player is None:
            run.warnings.append(
                _("%(player)s was deleted after the preview and was left out.")
                % {"player": item["name"]}
            )
            continue
        values = {
            name: int(value) if name in _INTEGER_FIELDS else Decimal(value)
            for name, value in item["values"].items()
        }
        stats = existing.get(player.pk)
        if stats is None:
            stats = PlayerSeasonStats(player=player, season=run.season)
            new.append(stats)
        else:
            changed.append(stats)
        stats.source_name = item["name"]
        stats.team_abbreviation = item["team"][:4]
        stats.import_run = run
        stats.updated_at = now
        for name, value in values.items():
            setattr(stats, name, value)
        written.add(player.pk)

    PlayerSeasonStats.objects.bulk_create(new)
    PlayerSeasonStats.objects.bulk_update(
        changed,
        ["source_name", "team_abbreviation", "import_run", "updated_at", *STAT_FIELDS],
        batch_size=200,
    )
    removed, _by_model = (
        PlayerSeasonStats.objects.filter(season=run.season).exclude(player_id__in=written).delete()
    )

    run.created, run.updated, run.removed = len(new), len(changed), removed
    run.matched = len(written)
    run.status = SeasonStatsImport.Status.IMPORTED
    run.imported_at = now
    run.payload = []
    run.save()
    # Older previews of this season counted against data that just changed.
    SeasonStatsImport.objects.filter(
        season=run.season, status=SeasonStatsImport.Status.PREVIEWED
    ).exclude(pk=run.pk).update(status=SeasonStatsImport.Status.EXPIRED, payload=[])


# --- reading stored seasons -----------------------------------------------------------

# Below this many games a per-game average says little: the player page does
# not rank such a season, and the players list can filter them out.
MIN_GAMES = 20


def newest_season():
    """The newest season with stored stats, e.g. "2025-26", or None.

    Labels sort correctly as text, so the maximum is the newest season.
    """
    return PlayerSeasonStats.objects.aggregate(newest=Max("season"))["newest"]


def short_season(label):
    """`2025-26` -> `25/26`, for column headers."""
    return f"{label[2:4]}/{label[5:7]}" if label else ""


def with_last_season(qs, season):
    """Annotate `last_season_fp` and `last_season_games` from `season`'s stats.

    Two correlated subqueries, each one index lookup on (player, season), so a
    list stays a single query however many players it shows. Players without
    a line get None. With no season the queryset comes back untouched.
    """
    if season is None:
        return qs
    line = PlayerSeasonStats.objects.filter(player=OuterRef("pk"), season=season)
    return qs.annotate(
        last_season_fp=Subquery(line.values("fantasy_points")[:1]),
        last_season_games=Subquery(line.values("games_played")[:1]),
    )
