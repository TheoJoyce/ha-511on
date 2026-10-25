"""Tests for working out what's in an area, a corridor or a road segment."""

from __future__ import annotations

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

from .common import EAST_401, KENORA, WEST_401
from .conftest import HOME

WEST = RoadSegment.from_api(WEST_401)
EAST = RoadSegment.from_api(EAST_401)
FAR = RoadSegment.from_api(KENORA)


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
    found = within_corridor(_events(), [*EAST.lines, *WEST.lines], width_km=5)
    nearest = [nearest_segment(e.coordinates, [EAST, WEST], 5) for e, _ in found]
    order = [0 if s is EAST else 1 for s in nearest if s is not None]
    assert order == sorted(order)
    assert all(km <= 5 for _, km in found)
    assert within_corridor(_events(), list(FAR.lines), width_km=5) == []
