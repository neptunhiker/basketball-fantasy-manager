"""Geometry for the points race on Analytics -> Insights.

One line per active roster plus the Dream Team, all in points on one axis
that starts at zero: a running total has a real zero, and every line starts
there. Days sit at equal steps rather than at their dates, so a day without
an import does not open a hole in the drawing -- it is simply not a day here.

Worked out on the server and drawn as inline SVG by
templates/analytics/partials/points_race_svg.html, like the app's other
charts. The hover readout (static/js/points_race.js) only reads coordinates
it is handed; nothing in the browser measures or scales data.

Built three times per page: a wide drawing and a tablet-sized one, each
with every line named at its end, and a narrow one for phones that leaves
the naming to the legend under it, since names beside the lines would leave
no room for the lines.
"""

import math
from itertools import pairwise

from django.utils.formats import number_format

from apps.nba.templatetags.player_stats import short_month_day
from apps.perf.charts import nice_top

# `end_labels`: how many characters of a name fit beside its line (0: none).
WIDE = {"width": 920, "height": 360, "max_labels": 7, "end_labels": 16}
# A tablet's card is about 700px wide: the wide drawing would shrink its text
# to 8px there, so it gets one of its own, drawn close to its real size.
MEDIUM = {"width": 680, "height": 330, "max_labels": 6, "end_labels": 13}
COMPACT = {"width": 360, "height": 300, "max_labels": 4, "end_labels": 0}

PAD_LEFT = 50
PAD_TOP = 14
PAD_BOTTOM = 30
EDGE_PAD = 12
TICKS = 4
# Room right of the plot for a dot and a name of `end_labels` characters.
CHAR_WIDTH = 6.4
END_LABEL_DOT = 14
# How far the first stepped date has to be from the start for the start to
# get a label as well: a start-anchored date and a centred one, about six
# characters each, with a little air between them.
START_LABEL_ROOM = 70
# Two names closer than this would overlap, so they are pushed apart.
END_LABEL_GAP = 15


def race_chart(race, width, height, max_labels, end_labels):
    """Coordinates for `insights.points_race(...)`, or None with fewer than two days."""
    days = race.days
    if len(days) < 2:
        return None

    plot_left = PAD_LEFT
    end_room = END_LABEL_DOT + math.ceil(end_labels * CHAR_WIDTH) + 6 if end_labels else 0
    plot_right = width - (end_room or EDGE_PAD)
    plot_top = PAD_TOP
    plot_bottom = height - PAD_BOTTOM
    plot_width = plot_right - plot_left
    plot_height = plot_bottom - plot_top

    highest = max(
        (float(value) for line in race.lines for value in line.values if value is not None),
        default=0,
    )
    top, step = nice_top(highest, TICKS)
    last = len(days) - 1

    def x_of(index):
        return round(plot_left + plot_width * index / last, 2)

    def y_of(value):
        if value is None:
            return None
        return round(plot_bottom - float(value) / top * plot_height, 2)

    xs = [x_of(index) for index in range(len(days))]
    lines = []
    for index, line in enumerate(race.lines):
        ys = [y_of(value) for value in line.values]
        lines.append(
            {
                # Its place in `race.lines`, which the readout and `hover.y` follow.
                "index": index,
                "line": line,
                "ys": ys,
                "path": _path(xs, ys),
                # A figure with no neighbour on either side draws no line: a dot.
                "lone": [
                    {"x": xs[index], "y": y}
                    for index, y in enumerate(ys)
                    if y is not None
                    and (index == 0 or ys[index - 1] is None)
                    and (index == last or ys[index + 1] is None)
                ],
            }
        )

    # Label the newest day and step back from it, so the latest always shows.
    every = max(1, math.ceil(len(days) / max_labels))
    x_labels = [
        {
            "x": xs[index],
            "label": short_month_day(day),
            # The outer two are pulled inside the drawing rather than clipped.
            "anchor": "start"
            if index == 0
            else "end"
            if index == last and not end_room
            else "middle",
        }
        for index, day in enumerate(days)
        if (last - index) % every == 0
    ]
    # The first day is where everyone starts at zero; worth a label of its
    # own unless the stepping put one right next to it.
    if x_labels[0]["x"] != xs[0] and x_labels[0]["x"] - xs[0] > START_LABEL_ROOM:
        x_labels.insert(0, {"x": xs[0], "label": short_month_day(days[0]), "anchor": "start"})

    y_ticks = [
        {"y": y_of(value), "value": number_format(round(value), force_grouping=True)}
        for value in (step * n for n in range(round(top / step) + 1))
    ]

    return {
        "width": width,
        "height": height,
        "plot_left": plot_left,
        "plot_right": plot_right,
        "plot_top": plot_top,
        "plot_bottom": plot_bottom,
        "plot_height": plot_height,
        "label_right": plot_left - 8,
        "label_bottom": plot_bottom + 20,
        "y_ticks": y_ticks,
        "x_labels": x_labels,
        # Drawing order: the grey rosters first, then the coloured ones, the
        # viewer's own above those and the Dream Team on top of everything.
        "lines": sorted(lines, key=_layer),
        "ends": _end_labels(lines, xs, plot_right, plot_top, plot_bottom, end_labels)
        if end_labels
        else [],
        "hover": {"x": xs, "y": [entry["ys"] for entry in lines]},
    }


def race_readout(race):
    """What the hover readout says at each day, shared by both drawings.

    The lines in the order of `race.lines` (the same order as the drawings'
    `hover.y`), and per day its label and every line's points, formatted.
    """
    return {
        "days": [short_month_day(day) for day in race.days],
        "lines": [
            {
                "name": line.name,
                "manager": line.manager,
                "color": line.color,
                "own": line.is_own,
                "dreamTeam": line.is_dream_team,
                "values": [None if value is None else float(value) for value in line.values],
                "labels": [
                    None if value is None else number_format(round(value), force_grouping=True)
                    for value in line.values
                ],
            }
            for line in race.lines
        ],
    }


def _layer(entry):
    line = entry["line"]
    return (line.is_dream_team, line.is_own, line.color != "muted")


def _path(xs, ys):
    """An SVG path through the days that have a figure, broken at the gaps."""
    parts = []
    pen_down = False
    for x, y in zip(xs, ys, strict=True):
        if y is None:
            pen_down = False
            continue
        parts.append(f"{'L' if pen_down else 'M'}{x} {y}")
        pen_down = True
    return " ".join(parts)


def _end_labels(lines, xs, plot_right, plot_top, plot_bottom, chars):
    """Each line's name beside its last figure, pushed apart where they crowd.

    Placed top to bottom with at least END_LABEL_GAP between neighbours; if
    that runs past the bottom of the plot, the whole stack moves up as far
    as it has to. The dot sits on the line's own height, the name beside it
    may be moved -- so a name always points at the line it belongs to.
    """
    ends = []
    for entry in lines:
        last = next(
            ((index, y) for index, y in reversed(list(enumerate(entry["ys"]))) if y is not None),
            None,
        )
        if last is None:
            continue
        line = entry["line"]
        name = line.name if len(line.name) <= chars else line.name[: chars - 1].rstrip() + "…"
        ends.append(
            {"line": line, "dot_x": xs[last[0]], "dot_y": last[1], "y": last[1], "name": name}
        )

    ends.sort(key=lambda end: end["y"])
    for previous, end in pairwise(ends):
        end["y"] = max(end["y"], previous["y"] + END_LABEL_GAP)
    overflow = ends[-1]["y"] - plot_bottom if ends else 0
    if overflow > 0:
        for end in ends:
            end["y"] -= overflow
        for end, later in reversed(list(pairwise(ends))):
            end["y"] = min(end["y"], later["y"] - END_LABEL_GAP)
    for end in ends:
        end["y"] = round(max(end["y"], plot_top), 2)
        end["x"] = plot_right + END_LABEL_DOT
        # A hairline from the dot to a name that had to move away from it.
        end["leader"] = (
            f"M{end['dot_x'] + 5} {end['dot_y']} L{end['x'] - 4} {end['y']}"
            if abs(end["y"] - end["dot_y"]) > 1
            else ""
        )
    return ends
