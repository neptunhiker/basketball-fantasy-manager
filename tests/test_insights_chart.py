"""The points race's drawing: `apps.analytics.charts`. Pure geometry, no database."""

import datetime as dt
from decimal import Decimal
from itertools import pairwise

from apps.analytics.charts import (
    COMPACT,
    END_LABEL_GAP,
    MEDIUM,
    WIDE,
    race_chart,
    race_readout,
)
from apps.analytics.insights import Line, Race


def days(count):
    return [dt.date(2026, 10, 20) + dt.timedelta(days=offset) for offset in range(count)]


def line(name, *values, **extra):
    return Line(
        key=name,
        name=name,
        values=[None if value is None else Decimal(value) for value in values],
        **extra,
    )


def race(*lines, count=None):
    count = count or len(lines[0].values)
    return Race(days=days(count), lines=list(lines))


def test_one_day_is_not_a_line():
    assert race_chart(race(line("A", 0), count=1), **WIDE) is None


def test_the_axis_starts_at_zero_and_ends_on_a_round_number():
    chart = race_chart(race(line("A", 0, 430, 870)), **WIDE)

    values = [tick["value"] for tick in chart["y_ticks"]]
    assert values[0] == "0"
    assert values[-1] == "1,000"
    assert chart["y_ticks"][0]["y"] == chart["plot_bottom"]
    assert chart["y_ticks"][-1]["y"] == chart["plot_top"]


def test_days_sit_at_equal_steps_from_edge_to_edge():
    chart = race_chart(race(line("A", 0, 10, 20, 30)), **WIDE)

    xs = chart["hover"]["x"]
    assert xs[0] == chart["plot_left"]
    assert xs[-1] == chart["plot_right"]
    steps = {round(b - a, 1) for a, b in pairwise(xs)}
    assert len(steps) == 1


def test_a_gap_breaks_the_line_and_a_lonely_figure_gets_a_dot():
    chart = race_chart(race(line("Dream", 0, 100, None, 300, None)), **WIDE)

    [drawn] = chart["lines"]
    assert drawn["path"].count("M") == 2
    assert [dot["x"] for dot in drawn["lone"]] == [chart["hover"]["x"][3]]


def test_the_dream_team_is_drawn_last_and_grey_rosters_first():
    chart = race_chart(
        race(
            line("Dream", 0, 100, is_dream_team=True, color="dt"),
            line("Mine", 0, 50, is_own=True, color="1"),
            line("Theirs", 0, 60, color="2"),
            line("Eighth", 0, 70, color="muted"),
        ),
        **WIDE,
    )

    assert [entry["line"].name for entry in chart["lines"]] == [
        "Eighth",
        "Theirs",
        "Mine",
        "Dream",
    ]


def test_end_labels_never_overlap_and_stay_inside_the_plot():
    lines = [line(f"Roster {index}", 0, 100 + index) for index in range(9)]
    chart = race_chart(race(*lines), **WIDE)

    ys = sorted(end["y"] for end in chart["ends"])
    assert len(ys) == 9
    assert all(b - a >= END_LABEL_GAP - 0.01 for a, b in pairwise(ys))
    assert ys[0] >= chart["plot_top"]
    assert ys[-1] <= chart["plot_bottom"]


def test_an_end_dot_stays_on_its_line_while_the_name_moves():
    chart = race_chart(race(line("A", 0, 500), line("B", 0, 501)), **WIDE)

    for end in chart["ends"]:
        assert end["dot_x"] == chart["plot_right"]
    a, b = sorted(chart["ends"], key=lambda end: end["line"].name)
    assert abs(a["dot_y"] - b["dot_y"]) < 1
    assert abs(a["y"] - b["y"]) >= END_LABEL_GAP - 0.01


def test_a_line_ending_early_is_labelled_where_it_ends():
    chart = race_chart(race(line("Dream", 0, 100, None), line("A", 0, 10, 20)), **WIDE)

    dream = next(end for end in chart["ends"] if end["line"].name == "Dream")
    assert dream["dot_x"] == chart["hover"]["x"][1]


def test_long_names_are_cut_beside_the_line():
    chart = race_chart(race(line("Bulla Ballers of the North", 0, 10)), **WIDE)

    [end] = chart["ends"]
    assert end["name"] == "Bulla Ballers o…"
    assert len(end["name"]) == WIDE["end_labels"]


def test_the_phone_drawing_leaves_naming_to_the_legend():
    chart = race_chart(race(line("A", 0, 10, 20)), **COMPACT)

    assert chart["ends"] == []
    assert chart["plot_right"] == COMPACT["width"] - 12


def test_x_labels_always_include_the_newest_day_and_the_start():
    chart = race_chart(race(line("A", *range(30))), **WIDE)

    xs = chart["hover"]["x"]
    labelled = [label["x"] for label in chart["x_labels"]]
    assert xs[-1] in labelled
    assert xs[0] in labelled
    assert len(labelled) <= WIDE["max_labels"] + 1


def test_the_readout_has_every_line_and_day_formatted():
    readout = race_readout(
        race(
            line("Dream", 0, 1234.4, is_dream_team=True, color="dt"),
            line("Mine", 0, None, is_own=True, color="1", manager="Bulla"),
        )
    )

    assert readout["days"] == ["20 Oct", "21 Oct"]
    dream, mine = readout["lines"]
    assert dream["labels"] == ["0", "1,234"]
    assert dream["dreamTeam"] is True
    assert mine["values"] == [0.0, None]
    assert (mine["manager"], mine["own"], mine["color"]) == ("Bulla", True, "1")


def test_the_start_gets_a_label_only_where_it_does_not_crowd_the_next():
    for size in (WIDE, MEDIUM, COMPACT):
        chart = race_chart(race(line("A", *range(22))), **size)
        xs = sorted(label["x"] for label in chart["x_labels"])
        assert all(b - a >= 45 for a, b in pairwise(xs)), (size, xs)
