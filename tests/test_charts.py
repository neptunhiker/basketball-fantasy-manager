"""The server-side chart geometry."""

from decimal import Decimal
from itertools import pairwise

from apps.nba import charts


def series(*values):
    return [(f"W{index}", value) for index, value in enumerate(values)]


def test_a_line_needs_two_ends():
    """One point is not a trend, and the caller should say so in words."""
    assert charts.line_chart([]) is None
    assert charts.line_chart(series(Decimal("20"))) is None
    assert charts.line_chart(series(None, Decimal("20"))) is None


def test_every_measured_point_becomes_a_dot():
    chart = charts.line_chart(series(Decimal("10"), Decimal("20"), Decimal("30")))
    assert len(chart["dots"]) == 3
    assert chart["line"].count(",") == 3


def test_higher_values_sit_higher_on_the_page():
    """SVG y grows downwards, so the best week must have the smallest y."""
    chart = charts.line_chart(series(Decimal("10"), Decimal("30")))
    low, high = chart["dots"]
    assert high["y"] < low["y"]


def test_the_axis_contains_the_data_and_ends_on_round_numbers():
    chart = charts.line_chart(series(Decimal("18.18"), Decimal("24.16")))
    assert chart["low"] <= Decimal("18.18")
    assert chart["high"] >= Decimal("24.16")
    assert chart["low"] == Decimal("18")
    assert chart["high"] == Decimal("26")


def test_every_gridline_carries_a_number():
    """The axis does not start at zero, so it may not be left to assumption."""
    chart = charts.line_chart(series(Decimal("18.18"), Decimal("24.16")))
    values = [tick["value"] for tick in chart["y_ticks"]]
    assert values == [Decimal("18"), Decimal("20"), Decimal("22"), Decimal("24"), Decimal("26")]
    assert all(chart["low"] <= value <= chart["high"] for value in values)


def test_a_flat_series_still_draws():
    """Six identical weeks are a real answer, not a division by zero."""
    chart = charts.line_chart(series(Decimal("20"), Decimal("20"), Decimal("20")))
    assert {dot["y"] for dot in chart["dots"]} == {chart["dots"][0]["y"]}
    assert chart["low"] < Decimal("20") < chart["high"]


def test_a_missing_week_widens_the_line_rather_than_shortening_the_season():
    """The gap keeps its place on the x axis: the season did not get shorter."""
    complete = charts.line_chart(series(Decimal("10"), Decimal("20"), Decimal("30")))
    gapped = charts.line_chart(series(Decimal("10"), None, Decimal("30")))
    assert len(gapped["dots"]) == 2
    # Both series end where the third week ends.
    assert gapped["dots"][-1]["x"] == complete["dots"][-1]["x"]
    assert gapped["dots"][0]["x"] == complete["dots"][0]["x"]


def test_every_week_keeps_its_label_even_without_a_figure():
    """The x axis is the calendar, not the list of weeks that were measured."""
    chart = charts.line_chart(series(Decimal("10"), None, Decimal("30")))
    assert [label["label"] for label in chart["x_labels"]] == ["W0", "W1", "W2"]


def test_the_outer_labels_are_pulled_inside_the_drawing():
    chart = charts.line_chart(series(Decimal("10"), Decimal("20"), Decimal("30")))
    anchors = [label["anchor"] for label in chart["x_labels"]]
    assert anchors == ["start", "middle", "end"]


def test_the_fill_closes_along_the_baseline():
    chart = charts.line_chart(series(Decimal("10"), Decimal("30")))
    assert chart["area"].startswith(f"M {chart['dots'][0]['x']},{chart['baseline']}")
    assert chart["area"].endswith("Z")


def test_the_step_scales_with_the_range():
    """Points per game and season totals cannot share one hard-coded step."""
    small = charts.line_chart(series(Decimal("0.4"), Decimal("1.1")))
    large = charts.line_chart(series(Decimal("120"), Decimal("780")))
    assert small["high"] - small["low"] <= Decimal("1")
    # A step of 200 over a range of 660, so the axis floors to zero here --
    # which is honest for season totals, since zero is where they started.
    assert (large["low"], large["high"]) == (Decimal("0"), Decimal("800"))


# --- reading a value off the line ---------------------------------------------


def test_every_point_owns_a_band_to_be_hovered_in():
    """A 3.5 pixel dot is not a hover target; the column above it is."""
    chart = charts.line_chart(series(Decimal("10"), Decimal("20"), Decimal("30")))
    assert all(dot["band_width"] > 20 for dot in chart["dots"])


def test_the_bands_tile_the_plot_without_gaps_or_overlaps():
    """Anywhere the pointer lands inside the drawing belongs to exactly one point."""
    chart = charts.line_chart(series(*[Decimal(value) for value in (10, 20, 15, 30, 25)]))
    edges = [(dot["band_x"], dot["band_x"] + dot["band_width"]) for dot in chart["dots"]]
    assert edges[0][0] == chart["plot_left"]
    assert round(edges[-1][1], 2) == chart["plot_right"]
    for (_, ends), (starts, _) in pairwise(edges):
        assert round(ends, 2) == round(starts, 2)


def test_a_border_sits_halfway_between_two_points():
    chart = charts.line_chart(series(Decimal("10"), Decimal("30")))
    first, second = chart["dots"]
    assert first["band_x"] + first["band_width"] == (first["x"] + second["x"]) / 2


def test_a_gapped_week_hands_its_space_to_its_neighbours():
    complete = charts.line_chart(series(Decimal("10"), Decimal("20"), Decimal("30")))
    gapped = charts.line_chart(series(Decimal("10"), None, Decimal("30")))
    assert len(gapped["dots"]) == 2
    assert sum(dot["band_width"] for dot in gapped["dots"]) == sum(
        dot["band_width"] for dot in complete["dots"]
    )


def test_the_readout_sits_above_its_dot():
    # Real figures, which land inside a rounded axis rather than on its edges:
    # 18,18 and 24,16 on an axis of 18 to 26.
    chart = charts.line_chart(series(Decimal("18.18"), Decimal("24.16")))
    assert all(dot["label_y"] < dot["y"] for dot in chart["dots"])


def test_a_readout_that_would_be_clipped_by_the_top_goes_below_instead():
    # Both values fall on step boundaries, so the axis fits them exactly and the
    # better of the two sits hard against the top of the drawing.
    chart = charts.line_chart(series(Decimal("10"), Decimal("20")))
    best = chart["dots"][-1]
    assert best["y"] == chart["plot_top"]
    assert best["label_y"] > best["y"]
    assert best["label_y"] < chart["baseline"]


def test_the_outer_readouts_are_anchored_inwards():
    """Centred text on the first and last points would hang off the drawing."""
    chart = charts.line_chart(series(Decimal("10"), Decimal("20"), Decimal("30")))
    assert [dot["label_anchor"] for dot in chart["dots"]] == ["start", "middle", "end"]


def test_scatter_chart_maps_x_and_y_to_the_plot():
    chart = charts.scatter_chart(
        [
            ("Low", Decimal("10"), Decimal("20")),
            ("High", Decimal("30"), Decimal("60")),
        ]
    )

    assert chart["dots"][0]["x"] < chart["dots"][1]["x"]
    assert chart["dots"][0]["y"] > chart["dots"][1]["y"]
    assert chart["x_low"] <= Decimal("10") <= chart["x_high"]
    assert chart["y_low"] <= Decimal("60") <= chart["y_high"]


def test_scatter_chart_omits_points_missing_either_measurement():
    chart = charts.scatter_chart(
        [("Missing x", None, Decimal("20")), ("Measured", Decimal("10"), Decimal("20"))]
    )

    assert [dot["label"] for dot in chart["dots"]] == ["Measured"]
    assert charts.scatter_chart([("Missing", None, Decimal("20"))]) is None


def test_scatter_chart_carries_expected_salary_for_tooltips():
    chart = charts.scatter_chart([("Measured", Decimal("10"), Decimal("20"), Decimal("18"))])

    assert chart["dots"][0]["expected_salary"] == Decimal("18")


def test_scatter_chart_carries_player_metadata():
    chart = charts.scatter_chart(
        [
            (
                "Measured",
                Decimal("10"),
                Decimal("20"),
                Decimal("18"),
                {"detail_url": "/players/measured/", "hotness": "7/10"},
            )
        ]
    )

    assert chart["dots"][0]["detail_url"] == "/players/measured/"
    assert chart["dots"][0]["hotness"] == "7/10"
