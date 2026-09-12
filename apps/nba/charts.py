"""Geometry for the small line charts in the templates.

Worked out here and emitted as inline SVG, rather than handed to a charting
library in the browser. Six weekly figures do not need a JavaScript dependency
the project would then have to vendor and keep current, and an SVG that is
already in the response draws with the page, prints, scales, and survives
JavaScript being switched off.

Nothing in here knows what it is plotting. It takes `(label, value)` pairs and
returns coordinates.
"""

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from itertools import pairwise

# The SVG's own coordinate space. The element itself is sized in CSS, so these
# numbers only set the aspect ratio and how much room the labels get.
WIDTH = 640
HEIGHT = 220
PAD_LEFT = 46
PAD_RIGHT = 14
PAD_TOP = 12
PAD_BOTTOM = 26

TICK_STEPS = (Decimal("1"), Decimal("2"), Decimal("5"), Decimal("10"))

# How far the hover readout sits from its dot, and how close to an edge a dot
# has to be before the readout is anchored inwards instead of centred.
LABEL_OFFSET = 14
LABEL_INSET = 40


def _nice_step(span, ticks):
    """A round number to step the axis by: 1, 2, 5, 10, 20, 50 and so on.

    Axis labels are read, not measured, so they should be numbers a person
    would choose -- 20 and 25, not 18,64 and 24,17.
    """
    rough = span / ticks
    if rough <= 0:
        return Decimal("1")

    magnitude = Decimal(10) ** int(rough.log10().to_integral_value(ROUND_FLOOR))
    for factor in TICK_STEPS:
        if magnitude * factor >= rough:
            return magnitude * factor
    return magnitude * 10


def _bounds(values, ticks):
    """An axis that contains every value and ends on round numbers."""
    low, high = min(values), max(values)
    if low == high:
        # A flat series still needs a range, or every point lands on one line
        # and the division below has nothing to divide by.
        low, high = low - 1, high + 1

    step = _nice_step(high - low, ticks)
    return (
        (low / step).to_integral_value(ROUND_FLOOR) * step,
        (high / step).to_integral_value(ROUND_CEILING) * step,
        step,
    )


def _add_hover_bands(dots):
    """Give every point a column of the chart to be hovered in.

    A 3.5 pixel dot is a poor target for a mouse and an impossible one for a
    trackpad in a hurry, so each point owns the vertical strip of the drawing
    that is nearer to it than to its neighbours -- the pointer only has to be
    somewhere above or below the dot for its value to appear.

    Written into the dots rather than returned, because a band is a property of
    the point it belongs to.
    """
    plot_right = WIDTH - PAD_RIGHT
    # Borders between neighbouring points sit halfway between them, so a gapped
    # week hands its space to the points either side of it.
    borders = [PAD_LEFT]
    borders += [round((left["x"] + right["x"]) / 2, 2) for left, right in pairwise(dots)]
    borders.append(plot_right)

    for index, dot in enumerate(dots):
        dot["band_x"] = borders[index]
        dot["band_width"] = round(borders[index + 1] - borders[index], 2)
        # The readout sits above its dot, except where that would push it off
        # the top of the drawing, in which case it goes below instead.
        above = dot["y"] - LABEL_OFFSET
        dot["label_y"] = above if above > PAD_TOP + LABEL_OFFSET else dot["y"] + LABEL_OFFSET + 4
        # And it is pulled inside the edges rather than being clipped by them.
        dot["label_anchor"] = (
            "start"
            if dot["x"] < PAD_LEFT + LABEL_INSET
            else "end"
            if dot["x"] > plot_right - LABEL_INSET
            else "middle"
        )


def line_chart(points, ticks=4):
    """A line chart over `(label, value)` pairs given oldest first.

    Points whose value is None are left out of the line rather than drawn at
    zero: a week nobody has measured is a gap, not a week of scoring nothing.
    Their place on the x axis is kept, so a gap shows as a longer stretch of
    line instead of silently shortening the season.

    Returns None when fewer than two figures survive. A line needs two ends,
    and a caller with one point should say so in words.
    """
    measured = [
        (index, label, Decimal(value))
        for index, (label, value) in enumerate(points)
        if value is not None
    ]
    if len(measured) < 2:
        return None

    low, high, step = _bounds([value for _, _, value in measured], ticks)
    plot_width = WIDTH - PAD_LEFT - PAD_RIGHT
    plot_height = HEIGHT - PAD_TOP - PAD_BOTTOM
    last_index = max(len(points) - 1, 1)

    def x_of(index):
        return round(PAD_LEFT + plot_width * index / last_index, 2)

    def y_of(value):
        return round(PAD_TOP + plot_height * float((high - value) / (high - low)), 2)

    dots = [
        {"x": x_of(index), "y": y_of(value), "label": label, "value": value}
        for index, label, value in measured
    ]
    _add_hover_bands(dots)

    tick_values = []
    value = low
    while value <= high:
        tick_values.append(value)
        value += step

    return {
        "line": " ".join(f"{dot['x']},{dot['y']}" for dot in dots),
        # Closed back along the baseline, for the faint fill under the line.
        "area": (
            f"M {dots[0]['x']},{HEIGHT - PAD_BOTTOM} "
            + " ".join(f"L {dot['x']},{dot['y']}" for dot in dots)
            + f" L {dots[-1]['x']},{HEIGHT - PAD_BOTTOM} Z"
        ),
        "dots": dots,
        "y_ticks": [{"y": y_of(value), "value": value} for value in tick_values],
        "x_labels": [
            {
                "x": x_of(index),
                "label": label,
                # The outer two labels are pulled inside the box so they do not
                # hang off the edge of the drawing.
                "anchor": "start" if index == 0 else "end" if index == last_index else "middle",
            }
            for index, (label, _) in enumerate(points)
        ],
        "width": WIDTH,
        "height": HEIGHT,
        "baseline": HEIGHT - PAD_BOTTOM,
        # Edges the template draws against, so the padding lives in one place
        # rather than as numbers repeated in the markup.
        "plot_left": PAD_LEFT,
        "plot_right": WIDTH - PAD_RIGHT,
        "plot_top": PAD_TOP,
        "plot_height": plot_height,
        "label_right": PAD_LEFT - 8,
        "label_bottom": HEIGHT - 8,
        "low": low,
        "high": high,
        "first": dots[0],
        "last": dots[-1],
    }


NEON_GREEN = "#39ff14"
NEON_RED = "#ff4d4d"
NEON_GRAY = "#a1a1aa"


def games_played_color(games_played):
    """Render neon green for healthy game volume, neon red for a short sample."""
    if games_played is None:
        return NEON_GRAY
    if games_played >= 6:
        return NEON_GREEN
    return NEON_RED


def scatter_chart(points, x_ticks=5, y_ticks=5):
    """Plot measured `(label, x, y[, expected_salary[, metadata]])` points.

    Missing x or y values are omitted. The returned geometry is deliberately
    unit-agnostic so callers can choose display units before handing data to
    the reusable scatter-chart template.
    """
    measured = []
    for point in points:
        label, x_value, y_value = point[:3]
        expected_salary = point[3] if len(point) > 3 else None
        metadata = point[4] if len(point) > 4 else {}
        if x_value is not None and y_value is not None:
            measured.append(
                (
                    label,
                    Decimal(x_value),
                    Decimal(y_value),
                    Decimal(expected_salary) if expected_salary is not None else None,
                    metadata,
                )
            )
    if not measured:
        return None

    x_low, x_high, x_step = _bounds([point[1] for point in measured], x_ticks)
    y_low, y_high, y_step = _bounds([point[2] for point in measured], y_ticks)
    plot_width = WIDTH - PAD_LEFT - PAD_RIGHT
    plot_height = HEIGHT - PAD_TOP - PAD_BOTTOM

    def x_of(value):
        return round(PAD_LEFT + plot_width * float((value - x_low) / (x_high - x_low)), 2)

    def y_of(value):
        return round(PAD_TOP + plot_height * float((y_high - value) / (y_high - y_low)), 2)

    dots = [
        {
            "x": x_of(x_value),
            "y": y_of(y_value),
            "label": label,
            "x_value": x_value,
            "y_value": y_value,
            "expected_salary": expected_salary,
            "color": games_played_color(metadata.get("games_played")),
            **metadata,
        }
        for label, x_value, y_value, expected_salary, metadata in measured
    ]

    def tick_values(low, high, step):
        values = []
        value = low
        while value <= high:
            values.append(value)
            value += step
        return values

    return {
        "dots": dots,
        "x_ticks": [
            {"x": x_of(value), "value": value} for value in tick_values(x_low, x_high, x_step)
        ],
        "y_ticks": [
            {"y": y_of(value), "value": value} for value in tick_values(y_low, y_high, y_step)
        ],
        "width": WIDTH,
        "height": HEIGHT,
        "plot_left": PAD_LEFT,
        "plot_right": WIDTH - PAD_RIGHT,
        "plot_top": PAD_TOP,
        "plot_bottom": HEIGHT - PAD_BOTTOM,
        "label_right": PAD_LEFT - 8,
        "label_bottom": HEIGHT - 8,
        "x_low": x_low,
        "x_high": x_high,
        "y_low": y_low,
        "y_high": y_high,
    }
