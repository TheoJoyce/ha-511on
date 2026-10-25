"""Tests for choosing generated items and suggesting commute routes."""

from __future__ import annotations

from unittest.mock import patch

from pytest_homeassistant_custom_component.common import load_json_array_fixture

from custom_components.ontario_511.api import Resource
from custom_components.ontario_511.coordinator import parse_items
from custom_components.ontario_511.generation import (
    cap_for,
    generate_for_area,
    generate_for_commute,
)
from custom_components.ontario_511.models import Item, RoadSegment

from .common import EAST_401, KENORA, WEST_401, road_condition
from .conftest import HOME

WEST = RoadSegment.from_api(WEST_401)
EAST = RoadSegment.from_api(EAST_401)
FAR = RoadSegment.from_api(KENORA)
SEGMENTS: dict[str, Item] = {s.id: s for s in (WEST, EAST, FAR)}


def _events() -> dict[str, Item]:
    return parse_items(Resource.EVENTS, load_json_array_fixture("event.json"))


def test_caps() -> None:
    """Cameras and road segments have their own caps; others share a default."""
    assert cap_for(Resource.ROAD_CONDITIONS) == 40
    assert cap_for(Resource.CAMERAS) == 25
    assert cap_for(Resource.CARPOOL_LOTS) == 25


def test_area_nearest_first_with_cap_and_exclusions() -> None:
    """Areas take the nearest items, skip excluded ones, and stop at the cap."""
    events = _events()
    everything = generate_for_area(Resource.EVENTS, events, HOME, 25)
    ids = list(everything.items)
    assert ids[:2] == ["323209", "322857"]

    with patch.dict(
        "custom_components.ontario_511.generation.GENERATION_CAPS",
        {Resource.EVENTS: 2},
    ):
        capped = generate_for_area(Resource.EVENTS, events, HOME, 25, exclude={ids[0]})
    assert list(capped.items) == ids[1:3]


def test_area_road_segments_count_when_passing_through() -> None:
    """A segment counts for an area when any part of it is inside."""
    generated = generate_for_area(Resource.ROAD_CONDITIONS, SEGMENTS, HOME, 15)
    assert set(generated.items) == {WEST.id, EAST.id}
    assert generated.items[WEST.id] == "Hwy 401: West"


def test_commute_route_segments_come_first() -> None:
    """A commute's own segments are generated before corridor neighbours."""
    parallel = RoadSegment.from_api(
        road_condition("Parallel", [(43.70, -79.50), (43.71, -79.30)], road="2")
    )
    data: dict[str, Item] = {**SEGMENTS, parallel.id: parallel}
    lines = [*EAST.lines, *WEST.lines]
    generated = generate_for_commute(
        Resource.ROAD_CONDITIONS, data, [EAST.id, WEST.id], lines, 5
    )
    assert list(generated.items) == [EAST.id, WEST.id, parallel.id]


def test_commute_items_follow_the_corridor() -> None:
    """Commute items are those within the corridor, in route order."""
    lines = [*WEST.lines, *EAST.lines]
    generated = generate_for_commute(
        Resource.EVENTS, _events(), [WEST.id, EAST.id], lines, 2
    )
    assert {"323209", "322857", "324420"} <= set(generated.items)
    assert "323770" not in generated.items


def test_commute_only_counts_the_driven_stretch() -> None:
    """Items beside the unused part of a long segment aren't generated."""
    driven = [WEST.lines[0][:2]]  # Only the western part of the segment.
    generated = generate_for_commute(Resource.EVENTS, _events(), [WEST.id], driven, 2)
    assert "323209" not in generated.items
    assert "324420" in generated.items
