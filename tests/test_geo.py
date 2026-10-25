"""Tests for geometry helpers."""

from __future__ import annotations

import pytest

from custom_components.ontario_511.geo import (
    BoundingBox,
    decode_polyline,
    distance_km,
    distance_to_line_km,
    distance_to_lines_km,
)

TORONTO = (43.6532, -79.3832)
OTTAWA = (45.4215, -75.6972)


def test_decode_polyline_reference_example() -> None:
    """Decodes the example from Google's polyline algorithm documentation."""
    assert decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@") == [
        (38.5, -120.2),
        (40.7, -120.95),
        (43.252, -126.453),
    ]


def test_decode_polyline_truncated() -> None:
    """A truncated polyline raises instead of returning garbage."""
    with pytest.raises(ValueError):
        decode_polyline("_p~iF~ps|U_")


def test_distance_km() -> None:
    """Toronto to Ottawa is about 352 km in a straight line."""
    assert distance_km(TORONTO, OTTAWA) == pytest.approx(352, abs=2)
    assert distance_km(TORONTO, TORONTO) == 0


@pytest.mark.parametrize(
    ("point", "expected"),
    [
        pytest.param((43.0, -79.5), 0.0, id="on_line"),
        pytest.param((43.009, -79.5), 1.0, id="1km_north_of_middle"),
        pytest.param((43.0, -78.0), 40.7, id="beyond_end"),
    ],
)
def test_distance_to_line(point: tuple[float, float], expected: float) -> None:
    """Distance is measured to the nearest point on the line, not a vertex."""
    line = [(43.0, -80.0), (43.0, -78.5)]
    assert distance_to_line_km(point, line) == pytest.approx(expected, abs=0.1)


def test_distance_to_lines_edge_cases() -> None:
    """Empty input is infinitely far; a single point is a point distance."""
    assert distance_to_lines_km(TORONTO, []) == float("inf")
    assert distance_to_line_km(TORONTO, [TORONTO]) == 0


def test_bounding_box_prefilter() -> None:
    """The box never rules out a point that's actually within range."""
    box = BoundingBox([(43.0, -80.0), (43.5, -79.0)])
    assert box.may_be_within((43.2, -79.5), 0.1)
    assert box.may_be_within((43.51, -79.5), 2.0)
    assert not box.may_be_within(OTTAWA, 50.0)
