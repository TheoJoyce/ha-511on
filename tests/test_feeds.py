"""Tests for combining a commute's corridor with the areas it links."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import load_json_array_fixture

from custom_components.ontario_511.api import Resource
from custom_components.ontario_511.coordinator import parse_items
from custom_components.ontario_511.feeds import Scope, _door_to_door
from custom_components.ontario_511.models import Alert, RoadSegment, TrafficEvent

from .common import EAST_401, KENORA, WEST_401

WEST = RoadSegment.from_api(WEST_401)
EAST = RoadSegment.from_api(EAST_401)
FAR = RoadSegment.from_api(KENORA)


def _events() -> dict[str, TrafficEvent]:
    parsed = parse_items(Resource.EVENTS, load_json_array_fixture("event.json"))
    return {k: v for k, v in parsed.items() if isinstance(v, TrafficEvent)}


def test_door_to_door_order_and_dedupe() -> None:
    """Start area, then the corridor, then the destination; each event once."""
    events = _events()
    start = Scope(centre=None, events=[(events["324420"], 1.0)], segments=[WEST])
    corridor = Scope(
        centre=None,
        events=[(events["324420"], 0.2), (events["323209"], 0.1)],
        segments=[WEST, EAST],
    )
    destination = Scope(centre=None, events=[(events["322857"], 2.0)], segments=[FAR])
    alerts = [
        a
        for a in parse_items(
            Resource.ALERTS, load_json_array_fixture("alerts.json")
        ).values()
        if isinstance(a, Alert)
    ]

    scope = _door_to_door(corridor, [start, destination], alerts)

    assert [e.id for e, _ in scope.events] == ["324420", "323209", "322857"]
    assert [s.id for s in scope.segments] == [WEST.id, EAST.id, FAR.id]
    assert scope.regions == {"central"}
    # One documented alert lists Central; the other only Northwest.
    assert [a.id for a in scope.alerts] == ["2001"]


def test_corridor_alone_when_areas_are_gone() -> None:
    """If the linked areas were deleted, the corridor still works."""
    events = _events()
    corridor = Scope(centre=None, events=[(events["323209"], 0.1)], segments=[WEST])
    scope = _door_to_door(corridor, [], [])
    assert [e.id for e, _ in scope.events] == ["323209"]
