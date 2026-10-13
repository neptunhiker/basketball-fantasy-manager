"""Official season stats: reading the file and finding our player for each line.

The file is the per-game table NBA.com exports for a season, one line per
player. Parsing knows nothing about the database and returns plain rows;
matching turns each row into one of our players, or reports why it cannot.
Nothing is ever guessed: a name that fits two players is reported, not picked.
"""

import csv
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.utils.translation import gettext as _

from .models import Player
from .services import normalize_name

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
        candidates = by_key.get(_ALIAS_KEYS.get(key, key), [])
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
