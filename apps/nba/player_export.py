"""The player list as an Excel workbook.

The file is the table the user is looking at: the same filters, the same
order, the same columns under the same conditions -- only every page at once,
and with raw numbers instead of formatted text, so a spreadsheet can sort and
add them. Salaries are stored in millions (3.2) and shown as the app shows them
(`$3.20M`).

Where the table stacks two facts in one cell (Latest FP with the change in
average and a DNP beneath, Next update with the new salary beneath), the
workbook gives each its own column: one fact per column is what a spreadsheet
can filter on.
"""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from io import BytesIO

from django.conf import settings
from django.urls import reverse
from django.utils import formats, timezone
from django.utils.dateparse import parse_date
from django.utils.text import slugify
from django.utils.translation import gettext as _
from django.utils.translation import ngettext, pgettext
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from apps.core.search import search_query
from apps.fantasy.models import Roster

from .models import HEALTHY, Player, Team
from .stats import hotness_reading

CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# The app's palette (assets/input.css), without the leading #.
NAVY = "1A1B41"  # tertiary-700
PRIMARY = "6A48DF"  # primary-700
PRIMARY_TINT = "F4F3FF"  # primary-50
BAND = "F7F7FA"
RULE = "D3D3D9"  # neutral-300
MUTED = "71717A"  # neutral-500
FAINT = "A1A1AA"  # neutral-400
GREEN = "047857"  # emerald-700
ROSE = "BE123C"  # rose-700
RED = "B91C1C"  # red-700
AMBER = "B45309"  # amber-700
SKY = "0369A1"  # sky-700
WHITE = "FFFFFF"

# Number formats, in Excel's invariant syntax: a German Excel shows `$3,20M`
# and an English one `$3.20M` from the same file. The dollar sign is quoted --
# bare, Apple's Numbers and Quick Look read it as "the local currency" and show
# euros -- and amounts are stored in millions rather than scaled for display
# with trailing commas, which only Excel understands.
MONEY = '"$"#,##0.00"M"'
SIGNED_MONEY = '"+$"#,##0.00"M";"-$"#,##0.00"M";"±$"0.00"M"'
POINTS = "#,##0.00"
ONE_DECIMAL = "#,##0.0"
WHOLE = "#,##0"
SIGNED_ONE = "+0.0;-0.0;0.0"
SIGNED_TWO = "+0.00;-0.00;0.00"
DATE = "d mmm yyyy"

# Installed on Windows, macOS, iOS and Android alike, and in Google Sheets: the
# sheet looks the same wherever it is opened. (Unnamed, Apple falls back to Times.)
FONT_NAME = "Arial"

# Where things sit on the sheet: a title block, then the table.
TITLE_ROW = 1
HEADER_ROW = 6
FIRST_DATA_ROW = HEADER_ROW + 1
# Watchlist mark and name stay put while the figures scroll, as on screen.
FROZEN_COLUMNS = 2


@dataclass(frozen=True)
class Column:
    header: str
    value: object  # callable: player -> cell value
    width: float
    number_format: str | None = None
    align: str = "right"
    color: object = None  # callable: (player, value) -> hex colour or None
    bold: bool = False
    sort_key: str | None = None


def _number(value):
    """Decimal to float for the cell; None stays empty."""
    if value is None:
        return None
    return float(value) if isinstance(value, Decimal) else value


def _millions(dollars):
    """A dollar amount as millions for the cell: 3_200_000 -> 3.2."""
    if dollars is None:
        return None
    return float(Decimal(dollars) / Decimal(1_000_000))


def _font(**style):
    return Font(name=FONT_NAME, **style)


def _sign_color(_player, value):
    if value is None or value == 0:
        return None
    return GREEN if value > 0 else ROSE


def _mark(player):
    if getattr(player, "is_kicked", False):
        return _("Kick list")
    if getattr(player, "is_watched", False):
        return "★ " + _("Watched")
    return None


def _mark_color(player, _value):
    if getattr(player, "is_kicked", False):
        return ROSE
    if getattr(player, "is_watched", False):
        return AMBER
    return None


def _team(player):
    return player.team.abbreviation if player.team else _("Free Agent")


def _hotness(player):
    reading = hotness_reading(player.hotness_score())
    if not reading:
        return None
    count = _("%(increases)s of %(comparisons)s") % {
        "increases": reading["increases"],
        "comparisons": reading["comparisons"],
    }
    label = {"hot": _("Hot"), "cold": _("Cold"), "steady": _("Steady")}.get(reading["label"])
    return f"{label} · {count}" if label else count


def _hotness_color(player, value):
    reading = hotness_reading(player.hotness_score())
    if not reading:
        return None
    return {"hot": GREEN, "cold": SKY}.get(reading["label"])


def _latest_game(player):
    """What the table says beneath Latest FP, as words: DNP, no game, played."""
    status = getattr(player, "latest_status", None)
    if status is None:
        return None
    if status.is_missed:
        if status.is_injured:
            return f"{_('DNP')} · {_('Injured')}"
        if status.is_day_to_day:
            return f"{_('DNP')} · {_('Day-to-day')}"
        return _("DNP")
    if status.is_partly:
        return ngettext("%(count)s DNP", "%(count)s DNP", status.missed) % {"count": status.missed}
    if status.is_no_game:
        return _("no game")
    return _("Played")


def _latest_game_color(player, _value):
    status = getattr(player, "latest_status", None)
    if status is None:
        return None
    if status.is_missed or status.is_partly:
        return AMBER
    if status.is_no_game:
        return FAINT
    return None


def _return(player):
    """The expected return date; "unknown" for a hurt player without one."""
    if player.expected_return:
        return player.expected_return
    if player.injury_state != HEALTHY:
        return _("unknown")
    return None


def _return_color(player, value):
    if isinstance(value, dt.date):
        return FAINT if value < timezone.localdate() else None
    return FAINT if value else None


def _injury(player):
    if player.is_injured:
        return _("Injured")
    if player.is_day_to_day:
        return _("Day-to-day")
    return _("Healthy")


def _injury_color(player, _value):
    if player.is_injured:
        return RED
    if player.is_day_to_day:
        return AMBER
    return GREEN


def columns(options):
    """The columns, in the table's order, under the table's conditions."""
    cols = [
        Column(_("Watchlist"), _mark, 12, align="left", color=_mark_color),
        Column(_("Player"), lambda p: p.full_name, 24, align="left", bold=True, sort_key="name"),
        Column(_("Position"), lambda p: p.position, 9, align="center", sort_key="position"),
        Column(_("Team"), _team, 10, align="left", sort_key="team"),
    ]
    if options["last_season"]:
        season = options["last_season_short"]
        min_games = options["last_season_min_games"]

        def _last_season_color(player, _value):
            # Too few games to read much into: shown, but quietly, as on screen.
            games = getattr(player, "last_season_games", None)
            return FAINT if games is not None and games < min_games else None

        cols += [
            Column(
                _("FP %(season)s") % {"season": season},
                lambda p: _number(getattr(p, "last_season_fp", None)),
                10,
                ONE_DECIMAL,
                color=_last_season_color,
                sort_key="last_fp",
            ),
            Column(
                _("Games %(season)s") % {"season": season},
                lambda p: getattr(p, "last_season_games", None),
                10,
                WHOLE,
                color=_last_season_color,
            ),
        ]
    cols += [
        Column(
            _("Actual salary"),
            lambda p: _millions(p.current_salary),
            12,
            MONEY,
            sort_key="salary",
        ),
        Column(
            _("Expected salary"),
            lambda p: _millions(p.predicted_salary_amount),
            12,
            MONEY,
            sort_key="expected",
        ),
        Column(
            _("Value"),
            lambda p: _millions(p.salary_difference_amount),
            12,
            SIGNED_MONEY,
            color=_sign_color,
            bold=True,
            sort_key="difference",
        ),
    ]
    if options["show_premium"]:
        cols.append(
            Column(
                _("Premium"),
                lambda p: _millions(p.pick_premium),
                11,
                SIGNED_MONEY,
                sort_key="premium",
            )
        )
    if options["salary_baseline"]:
        cols += [
            Column(
                _("Next update"),
                lambda p: _millions(p.outlook_change),
                12,
                SIGNED_MONEY,
                color=_sign_color,
                bold=True,
                sort_key="outlook",
            ),
            Column(_("Salary after the update"), lambda p: _millions(p.outlook_salary), 13, MONEY),
        ]
    cols += [
        Column(_("Points"), lambda p: _number(p.current_total_fp), 10, POINTS, sort_key="points"),
        Column(
            _("FP/$M"),
            lambda p: _number(p.fp_per_million),
            9,
            ONE_DECIMAL,
            sort_key="fp_per_million",
        ),
        Column(
            _("Avg/game"),
            lambda p: _number(p.current_fp_per_game),
            10,
            POINTS,
            bold=True,
            sort_key="avg",
        ),
    ]
    if options["show_rest_of_season"]:
        days = options["next_days"]
        cols += [
            Column(
                _("Proj. FP"),
                lambda p: _number(p.projected_fp),
                10,
                WHOLE,
                bold=True,
                sort_key="projected",
            ),
            Column(
                pgettext("games left", "Left"),
                lambda p: p.games_expected,
                8,
                WHOLE,
                sort_key="left",
            ),
            Column(
                ngettext("%(days)s day", "%(days)s days", days) % {"days": days},
                lambda p: p.next_days,
                8,
                WHOLE,
                align="center",
                sort_key="week",
            ),
        ]
    cols += [
        Column(
            _("Latest FP"),
            lambda p: _number(p.latest_fp_gain),
            10,
            SIGNED_ONE,
            color=lambda p, v: ROSE if v is not None and v < 0 else None,
            sort_key="latest",
        ),
        Column(
            _("Change in points per game"),
            lambda p: _number(p.latest_avg_change),
            13,
            SIGNED_TWO,
            color=_sign_color,
        ),
        Column(_("Latest game"), _latest_game, 15, align="left", color=_latest_game_color),
        Column(_("Games"), lambda p: p.current_games_played, 8, WHOLE, sort_key="games"),
        Column(_("Hotness"), _hotness, 14, align="left", color=_hotness_color, sort_key="hotness"),
        Column(_("Return"), _return, 12, DATE, color=_return_color, sort_key="return"),
        Column(_("Injury"), _injury, 12, align="left", color=_injury_color, bold=True),
    ]
    if options["show_status"]:
        cols.append(
            Column(
                _("Status"),
                lambda p: _("Active") if p.is_active else _("Inactive"),
                10,
                align="left",
                color=lambda p, v: None if p.is_active else FAINT,
            )
        )
    return cols


# --- what the user asked for, in words ---------------------------------------


def _date(value):
    try:
        parsed = parse_date(value.strip())
    except ValueError:
        return None
    return formats.date_format(parsed, "SHORT_DATE_FORMAT") if parsed else None


def describe_filters(
    request, *, watchlist_page, kicklist_only, last_season_short, min_games, next_days
):
    """The active filters as short phrases, in the filter bar's order."""
    get = request.GET.get
    parts = []
    if query := search_query(request.GET):
        parts.append(f"{_('Search by name')}: “{query}”")
    if (position := get("position")) in Player.Position.values:
        parts.append(f"{_('Position')}: {Player.Position(position).label}")
    if abbreviation := get("team", "").strip():
        team = Team.objects.filter(abbreviation=abbreviation).first()
        if team:
            parts.append(f"{_('Team')}: {team.name}")
    injury = {"healthy": _("Healthy"), "day_to_day": _("Day-to-day"), "injured": _("Injured")}
    if label := injury.get((get("injury_status") or "").strip().lower()):
        parts.append(f"{_('Injury status')}: {label}")
    if roster_id := get("roster", "").strip():
        roster = Roster.objects.filter(id=roster_id, manager__user=request.user).first()
        if roster:
            parts.append(f"{_('Roster')}: {roster.name}")
    try:
        max_salary = Decimal(get("max_salary", ""))
    except ArithmeticError:
        max_salary = None
    if max_salary is not None and max_salary.is_finite() and max_salary >= 0:
        parts.append(_("Max salary ($M)") + f": {formats.number_format(max_salary)}")
    if return_from := _date(get("return_from", "")):
        parts.append(f"{_('Back from')} {return_from}")
    if return_to := _date(get("return_to", "")):
        parts.append(f"{_('Back by')} {return_to}")
    if get("inactive"):
        parts.append(_("Include inactive players"))
    if get("min_games") == "6":
        parts.append(_("At least 6 games"))
    if (week := get("week_games")) in ("3", "4"):
        window = ngettext("Next %(days)s day", "Next %(days)s days", next_days) % {
            "days": next_days
        }
        games = _("at least 3 games") if week == "3" else _("at least 4 games")
        parts.append(f"{window}: {games}")
    if last_season_short and get("last_min_games") == "1":
        parts.append(
            _("At least %(games)s games in %(season)s")
            % {"games": min_games, "season": last_season_short}
        )
    if not watchlist_page:
        if kicklist_only:
            parts.append(_("Kick list only"))
        elif get("watchlist") == "1":
            parts.append(_("Watched only"))
    return parts


def describe_sort(cols, sort, descending):
    """ "Avg/game, highest first" -- the order the rows are in."""
    label = next((col.header for col in cols if col.sort_key == sort), None)
    if label is None:
        return None
    if sort in ("name", "position", "team"):
        direction = _("Z–A") if descending else _("A–Z")  # noqa: RUF001
    elif sort == "return":
        direction = _("latest first") if descending else _("earliest first")
    else:
        direction = _("highest first") if descending else _("lowest first")
    return f"{label}, {direction}"


# --- the workbook ------------------------------------------------------------


def _legend(options):
    """Column -> what it means: the table's footnote, one line per column."""
    rows = [
        (
            _("Actual salary"),
            _(
                "Salaries are in millions of dollars: 3.2 is $3.2M. Every figure is a "
                "number you can sort and add."
            ),
        ),
        (
            _("Value"),
            _("expected salary for his points per game minus actual salary — + means a bargain."),
        ),
    ]
    if options["show_premium"]:
        rows.append(
            (
                _("Premium"),
                _(
                    "salary minus expected salary right after the last update — the part picks "
                    "explain."
                ),
            )
        )
    if options["salary_baseline"]:
        rows.append(
            (
                _("Next update"),
                _(
                    "expected salary for his points per game now plus his pick premium, against "
                    "today's salary."
                ),
            )
        )
    rows += [
        (
            _("FP/$M"),
            _("season points per $1M of current salary — missed games pull it down."),
        ),
        (_("Hotness"), _("how often his average rose over his last 10 games.")),
        (
            _("Latest FP"),
            _(
                # The same words as the table's footnote, so one translation serves both.
                "points since the previous snapshot — DNP: his team played without him, – : "  # noqa: RUF001
                "his team had no game."
            ),
        ),
    ]
    if options["show_rest_of_season"]:
        rows.append(
            (
                _("Proj. FP"),
                _("points per game × games he can still play × %(percent)s%% availability.")  # noqa: RUF001
                % {"percent": options["availability_percent"]},
            )
        )
    if options["last_season"]:
        rows.append(
            (
                _("FP %(season)s") % {"season": options["last_season_short"]},
                _("fantasy points per game last season, by this game's rules."),
            )
        )
    return rows


def _sheet_title(text):
    """Excel allows 31 characters and none of []:*?/\\ in a sheet name."""
    for char in "[]:*?/\\":
        text = text.replace(char, " ")
    return text[:31]


def build_workbook(players, *, request, options, title, filters, sort_text):
    """The export as xlsx bytes."""
    cols = columns(options)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = _sheet_title(title)
    sheet.sheet_properties.tabColor = PRIMARY
    sheet.sheet_view.showGridLines = False
    sheet.sheet_view.zoomScale = 110
    workbook.properties.title = title
    workbook.properties.creator = settings.SITE_NAME

    last_col = len(cols)
    last_letter = get_column_letter(last_col)

    # Title block: what this is, when it was taken, and from which view.
    now = timezone.localtime()
    count = ngettext("%(player_count)s player", "%(player_count)s players", len(players)) % {
        "player_count": len(players)
    }
    exported = _("Exported %(moment)s") % {
        "moment": formats.date_format(now, "SHORT_DATETIME_FORMAT")
    }
    sheet.cell(TITLE_ROW, 1, title).font = _font(size=18, bold=True, color=NAVY)
    sheet.row_dimensions[TITLE_ROW].height = 30
    meta = sheet.cell(TITLE_ROW + 1, 1, f"{count}  ·  {exported}  ·  {settings.SITE_NAME}")
    meta.font = _font(size=10, color=MUTED)
    filter_text = " · ".join(filters) if filters else _("none")
    filter_cell = sheet.cell(TITLE_ROW + 2, 1, f"{_('Filters')}: {filter_text}")
    filter_cell.font = _font(size=10, color=MUTED)
    if sort_text:
        sort_cell = sheet.cell(TITLE_ROW + 3, 1, f"{_('Sorted by')}: {sort_text}")
        sort_cell.font = _font(size=10, color=MUTED)
    # A brand-coloured rule under the title block.
    sheet.row_dimensions[HEADER_ROW - 1].height = 6
    for index in range(1, last_col + 1):
        sheet.cell(HEADER_ROW - 1, index).border = Border(
            bottom=Side(style="medium", color=PRIMARY)
        )

    # Header.
    header_fill = PatternFill("solid", fgColor=NAVY)
    header_rule = Border(bottom=Side(style="thin", color=NAVY))
    for index, col in enumerate(cols, start=1):
        cell = sheet.cell(HEADER_ROW, index, col.header)
        cell.font = _font(bold=True, color=WHITE, size=10)
        cell.fill = header_fill
        cell.border = header_rule
        cell.alignment = Alignment(
            horizontal=col.align,
            vertical="center",
            wrap_text=True,
            indent=0 if col.align == "center" else 1,
        )
        sheet.column_dimensions[get_column_letter(index)].width = col.width
    sheet.row_dimensions[HEADER_ROW].height = 34

    # Rows.
    band = PatternFill("solid", fgColor=BAND)
    hairline = Border(bottom=Side(style="hair", color=RULE))
    # An indent keeps text off the number in the cell next to it.
    alignments = {
        align: Alignment(horizontal=align, vertical="center", indent=0 if align == "center" else 1)
        for align in ("left", "center", "right")
    }
    for offset, player in enumerate(players):
        row = FIRST_DATA_ROW + offset
        for index, col in enumerate(cols, start=1):
            value = col.value(player)
            cell = sheet.cell(row, index, value)
            if col.number_format and not isinstance(value, str):
                cell.number_format = col.number_format
            color = col.color(player, value) if col.color else None
            cell.font = _font(size=10, bold=col.bold, color=color or NAVY)
            cell.alignment = alignments[col.align]
            cell.border = hairline
            if offset % 2:
                cell.fill = band
        # The name links back to the player's page.
        name = sheet.cell(row, 2)
        name.hyperlink = request.build_absolute_uri(
            reverse("nba:player-detail", args=[player.slug])
        )
        name.font = _font(size=10, bold=True, color=PRIMARY)
        sheet.row_dimensions[row].height = 18

    last_row = FIRST_DATA_ROW + len(players) - 1
    if players:
        sheet.auto_filter.ref = f"A{HEADER_ROW}:{last_letter}{last_row}"
    else:
        empty = sheet.cell(FIRST_DATA_ROW, 1, _("Nothing matches those filters."))
        empty.font = _font(size=10, italic=True, color=MUTED)

    sheet.freeze_panes = sheet.cell(FIRST_DATA_ROW, FROZEN_COLUMNS + 1)

    # Printing: landscape, one page wide, the header on every page.
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.print_title_rows = f"{HEADER_ROW}:{HEADER_ROW}"
    sheet.print_options.horizontalCentered = True
    sheet.page_margins.left = sheet.page_margins.right = 0.4
    sheet.oddFooter.center.text = "&P / &N"
    sheet.oddFooter.center.color = MUTED

    _write_legend(workbook, options)

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _write_legend(workbook, options):
    sheet = workbook.create_sheet(_sheet_title(_("Legend")))
    sheet.sheet_view.showGridLines = False
    sheet.sheet_properties.tabColor = NAVY
    sheet.column_dimensions["A"].width = 22
    sheet.column_dimensions["B"].width = 90
    sheet.cell(1, 1, _("Legend")).font = _font(size=18, bold=True, color=NAVY)
    sheet.row_dimensions[1].height = 30
    header_fill = PatternFill("solid", fgColor=NAVY)
    for index, text in enumerate((_("Column"), _("Meaning")), start=1):
        cell = sheet.cell(3, index, text)
        cell.font = _font(bold=True, color=WHITE, size=10)
        cell.fill = header_fill
        cell.alignment = Alignment(vertical="center")
    sheet.row_dimensions[3].height = 24
    band = PatternFill("solid", fgColor=PRIMARY_TINT)
    for offset, (label, meaning) in enumerate(_legend(options)):
        row = 4 + offset
        label_cell = sheet.cell(row, 1, label)
        label_cell.font = _font(size=10, bold=True, color=NAVY)
        meaning_cell = sheet.cell(row, 2, meaning[:1].upper() + meaning[1:])
        meaning_cell.font = _font(size=10, color=NAVY)
        for cell in (label_cell, meaning_cell):
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if offset % 2:
                cell.fill = band
        sheet.row_dimensions[row].height = 30


def filename(title):
    """`players-2026-11-13.xlsx`, in the user's language."""
    return f"{slugify(title) or 'players'}-{timezone.localdate():%Y-%m-%d}.xlsx"
