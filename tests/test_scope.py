"""Tests for working out what's in an area, a corridor or a road segment."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import load_json_array_fixture

from custom_components.ontario_511.api import Resource
from custom_components.ontario_511.coordinator import parse_items
from custom_components.ontario_511.models import RoadSegment, TrafficEvent
from custom_components.ontario_511.scope import (
    attach_to_segments,
    nearest_segment,
    segments_within_radius,
    within_corridor,
    within_radius,
)

from .conftest import HOME


def _encode(points: Sequence[tuple[float, float]]) -> str:
    """Encode points with Google's polyline algorithm (test helper)."""
    out, prev = [], (0, 0)
    for lat, lon in points:
        cur = (round(lat * 1e5), round(lon * 1e5))
        for delta in (cur[0] - prev[0], cur[1] - prev[1]):
            value = ~(delta << 1) if delta < 0 else delta << 1
            while value >= 0x20:
                out.append(chr((0x20 | (value & 0x1F)) + 63))
                value >>= 5
            out.append(chr(value + 63))
        prev = cur
    return "".join(out)


def _segment(name: str, points: Sequence[tuple[float, float]]) -> RoadSegment:
    item: dict[str, Any] = {
        "RoadwayName": "401",
        "LocationDescription": name,
        "Condition": ["Bare and dry road"],
        "EncodedPolyline": [_encode(points)],
    }
    return RoadSegment.from_api(item)


# Roughly Highway 401 across north Toronto, split into a west and east part.
WEST = _segment("West", [(43.715, -79.60), (43.725, -79.50), (43.73, -79.44)])
EAST = _segment("East", [(43.73, -79.44), (43.745, -79.40), (43.77, -79.25)])
FAR = _segment("Kenora", [(49.77, -94.5), (49.78, -94.4)])


def _events() -> list[TrafficEvent]:
    parsed = parse_items(Resource.EVENTS, load_json_array_fixture("event.json"))
    return [e for e in parsed.values() if isinstance(e, TrafficEvent)]


def test_within_radius_sorts_and_filters() -> None:
    """Only items with coordinates inside the radius, nearest first."""
    found = within_radius(_events(), HOME, 25)
    distances = [km for _, km in found]
    ids = {item.id for item, _ in found}
    assert distances == sorted(distances)
    assert all(km <= 25 for km in distances)
    assert {"323209", "322857", "321777", "322863", "324420"} <= ids
    assert not ids & {"323770", "275070"}


def test_segments_within_radius() -> None:
    """Segments count when any part of them passes inside the radius."""
    found = segments_within_radius([FAR, EAST, WEST], HOME, 15)
    assert {s.id for s, _ in found} == {EAST.id, WEST.id}


def test_attach_to_segments() -> None:
    """Events attach to the nearest segment within 2 km and nowhere else."""
    attached = attach_to_segments(_events(), [WEST, EAST, FAR])
    assert set(attached) <= {WEST.id, EAST.id}
    attached_ids = {e.id for events in attached.values() for e in events}
    assert {"323209", "322857", "324420"} <= attached_ids
    assert "323770" not in attached_ids
    assert "275070" not in attached_ids


@pytest.mark.parametrize(
    ("point", "expected"),
    [
        pytest.param((43.726, -79.50), "West", id="on_west"),
        pytest.param((43.76, -79.30), "East", id="on_east"),
        pytest.param((43.90, -79.40), None, id="too_far"),
    ],
)
def test_nearest_segment(point: tuple[float, float], expected: str | None) -> None:
    """The nearest segment within range wins."""
    segment = nearest_segment(point, [WEST, EAST, FAR])
    assert (segment.description if segment else None) == expected


def test_within_corridor_follows_route_order() -> None:
    """Corridor items are ordered by the route segment they're nearest to."""
    found = within_corridor(_events(), [EAST, WEST], width_km=5)
    nearest = [nearest_segment(e.coordinates, [EAST, WEST], 5) for e, _ in found]
    order = [0 if s is EAST else 1 for s in nearest if s is not None]
    assert order == sorted(order)
    assert all(km <= 5 for _, km in found)
    assert within_corridor(_events(), [FAR], width_km=5) == []
