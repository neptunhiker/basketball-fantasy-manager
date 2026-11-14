"""Geometry for the dashboard's trend chart: p95 and p50 over time.

Two series in the same unit on one axis, starting at zero -- a duration has
a real zero, and a fitted axis would make 40 ms against 60 ms look like a
crisis. Every bucket owns an equal column of the drawing, which is both where
its point sits and the area that shows its values on hover. An empty bucket
breaks the line rather than bridging it, so a quiet night does not read as a
fast one.

Built twice per page, wide and narrow, because a 720-unit drawing squeezed
onto a phone shrinks its labels to nothing.

Deploys are dashed lines at the moment they went live, inside their bucket's
column. A window with any gets a band above the plot, so the hover readout
has a third line to name them.
"""

import math

# `subject_chars`: how much of a commit subject fits in the hover readout.
WIDE = {"width": 720, "height": 260, "max_labels": 8, "end_labels": True, "subject_chars": 56}
NARROW = {"width": 360, "height": 240, "max_labels": 4, "end_labels": False, "subject_chars": 16}

PAD_LEFT = 52
PAD_TOP = 44  # room for the hover readout above the plot
DEPLOY_BAND = 16  # and for its line about deploys, when the window has any
PAD_BOTTOM = 28
END_LABEL_ROOM = 40
EDGE_PAD = 12
# Two end labels closer than this would overlap, so they are pushed apart.
END_LABEL_GAP = 14
TICKS = 4


def nice_top(value, ticks=TICKS):
    """The axis maximum: a round number at or above `value`, and its step."""
    if value <= 0:
        return 100.0, 25.0
    rough = value / ticks
    magnitude = 10 ** math.floor(math.log10(rough))
    step = next(magnitude * factor for factor in (1, 2, 2.5, 5, 10) if magnitude * factor >= rough)
    return math.ceil(value / step) * step, step


def _path(points, key):
    """An SVG path through the points that have `key`, broken at the gaps."""
    parts = []
    pen_down = False
    for point in points:
        if point[key] is None:
            pen_down = False
            continue
        parts.append(f"{'L' if pen_down else 'M'}{point['x']} {point[key]}")
        pen_down = True
    return " ".join(parts)


def _alone(points, index, key):
    """True for a point no line reaches: it needs a dot of its own to be seen."""
    before = points[index - 1][key] if index > 0 else None
    after = points[index + 1][key] if index + 1 < len(points) else None
    return points[index][key] is not None and before is None and after is None


def _deploy_x(bucket, index, plot_left, column):
    """Where the bucket's first deploy sits: as far into its column as the
    deploy was into the bucket."""
    start, end = bucket["bucket"], bucket["end"]
    share = (bucket["deploys"][0].deployed_at - start) / (end - start)
    return round(plot_left + (index + min(max(share, 0), 1)) * column, 2)


def trend_chart(buckets, width, height, max_labels, end_labels, subject_chars):
    """Coordinates for `stats.trend(...)`'s buckets, or None with nothing measured."""
    measured = [b[key] for b in buckets for key in ("p95", "p50") if b[key] is not None]
    if not measured:
        return None

    has_deploys = any(bucket.get("deploys") for bucket in buckets)
    band = DEPLOY_BAND if has_deploys else 0
    height += band
    plot_left = PAD_LEFT
    plot_right = width - (END_LABEL_ROOM if end_labels else EDGE_PAD)
    plot_top = PAD_TOP + band
    plot_bottom = height - PAD_BOTTOM
    plot_width = plot_right - plot_left
    plot_height = plot_bottom - plot_top
    top, step = nice_top(max(measured))

    def y_of(value):
        return None if value is None else round(plot_bottom - value / top * plot_height, 2)

    column = plot_width / len(buckets)
    points = []
    for index, bucket in enumerate(buckets):
        x = round(plot_left + (index + 0.5) * column, 2)
        points.append(
            {
                **bucket,
                "x": x,
                "y95": y_of(bucket["p95"]),
                "y50": y_of(bucket["p50"]),
                "band_x": round(plot_left + index * column, 2),
                "band_width": round(column, 2),
                # The readout is pulled inside the edges rather than clipped.
                "anchor": (
                    "start"
                    if x < plot_left + plot_width * 0.2
                    else "end"
                    if x > plot_right - plot_width * 0.2
                    else "middle"
                ),
            }
        )
    for index, point in enumerate(points):
        point["alone95"] = _alone(points, index, "y95")
        point["alone50"] = _alone(points, index, "y50")

    # One line per bucket, however many deploys it holds: in a day's column
    # two lines an hour apart would draw as one thick smudge.
    deploys = [
        {
            "x": _deploy_x(bucket, index, plot_left, column),
            "count": len(bucket["deploys"]),
            "deploys": bucket["deploys"],
        }
        for index, bucket in enumerate(buckets)
        if bucket.get("deploys")
    ]

    # Label the newest bucket and step back from it, so "today" always shows.
    every = max(1, math.ceil(len(points) / max_labels))
    x_labels = [
        {"x": point["x"], "at": point["bucket"]}
        for index, point in enumerate(points)
        if (len(points) - 1 - index) % every == 0
    ]

    y_ticks = [
        {"value": value, "y": y_of(value)}
        for value in (step * n for n in range(round(top / step) + 1))
    ]

    ends = None
    if end_labels:
        last95 = next((p for p in reversed(points) if p["y95"] is not None), None)
        last50 = next((p for p in reversed(points) if p["y50"] is not None), None)
        if last95 and last50:
            y95, y50 = last95["y95"], last50["y50"]
            # p95 is never below p50, so it is the one that moves up.
            if y50 - y95 < END_LABEL_GAP:
                middle = (y95 + y50) / 2
                y95, y50 = middle - END_LABEL_GAP / 2, middle + END_LABEL_GAP / 2
            ends = {"x": plot_right + 8, "y95": round(y95, 2), "y50": round(y50, 2)}

    return {
        "width": width,
        "height": height,
        "plot_left": plot_left,
        "plot_right": plot_right,
        "plot_top": plot_top,
        "plot_bottom": plot_bottom,
        "plot_height": plot_height,
        "label_right": plot_left - 8,
        "label_bottom": plot_bottom + 18,
        # The readout stays at the top; a deploy band goes between it and the plot.
        "readout_y": PAD_TOP - 26,
        "readout_note_y": PAD_TOP - 11,
        "readout_deploy_y": PAD_TOP + 4,
        "subject_chars": subject_chars,
        "deploys": deploys,
        "has_deploys": has_deploys,
        "y_ticks": y_ticks,
        "x_labels": x_labels,
        "p95_path": _path(points, "y95"),
        "p50_path": _path(points, "y50"),
        "points": points,
        "ends": ends,
    }


def trend_charts(buckets):
    """The wide and the narrow drawing of the same buckets."""
    return {
        "wide": trend_chart(buckets, **WIDE),
        "narrow": trend_chart(buckets, **NARROW),
    }
