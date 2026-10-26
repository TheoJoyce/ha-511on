"""Tests for parsing every resource into models."""

from __future__ import annotations

import dataclasses
from datetime import timedelta
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import load_json_array_fixture
from syrupy.assertion import SnapshotAssertion

from custom_components.ontario_511.api import Resource
from custom_components.ontario_511.conditions import RoadCondition
from custom_components.ontario_511.coordinator import parse_items
from custom_components.ontario_511.models import (
    FerryTerminal,
    Item,
    RestArea,
    RoadSegment,
    SeasonalLoad,
    TrafficEvent,
)

from .common import road_condition

# Geometry is checked separately; snapshotting every polyline point would make
# the snapshots unreadable.
_SKIP_FIELDS = {"lines", "bbox"}


def _summary(item: Item) -> dict[str, Any]:
    return {
        field.name: getattr(item, field.name)
        for field in dataclasses.fields(item)
        if field.name not in _SKIP_FIELDS
    }


def _parse(resource: Resource) -> dict[str, Item]:
    return parse_items(resource, load_json_array_fixture(f"{resource.value}.json"))


@pytest.mark.parametrize("resource", list(Resource), ids=lambda r: r.value)
def test_parse_fixture(resource: Resource, snapshot: SnapshotAssertion) -> None:
    """Every fixture item parses, with a unique ID, into the snapshotted fields."""
    raw = load_json_array_fixture(f"{resource.value}.json")
    parsed = _parse(resource)
    assert len(parsed) == len(raw)
    assert [_summary(item) for item in parsed.values()] == snapshot


def test_malformed_items_are_skipped() -> None:
    """Items missing their ID are dropped instead of failing the whole update."""
    parsed = parse_items(
        Resource.EVENTS,
        [{"RoadwayName": "HWY 401"}, {"ID": None}, {"ID": 7, "Latitude": "x"}],
    )
    assert list(parsed) == ["7"]
    assert parsed["7"].coordinates is None


def test_flipped_longitude_has_no_location() -> None:
    """The live event published with a positive longitude gets no coordinates."""
    events = _parse(Resource.EVENTS)
    assert events["275070"].coordinates is None


def test_road_segments() -> None:
    """Segments get derived IDs, ranked conditions, and thinned geometry."""
    segments = list(_parse(Resource.ROAD_CONDITIONS).values())
    assert all(isinstance(s, RoadSegment) for s in segments)
    by_condition = {s.condition for s in segments if isinstance(s, RoadSegment)}
    assert RoadCondition.UNKNOWN in by_condition
    assert RoadCondition.WET in by_condition
    for segment in segments:
        assert isinstance(segment, RoadSegment)
        assert segment.lines
        assert segment.bbox is not None
        assert segment.coordinates is not None


def test_ferry_status_casing() -> None:
    """The undocumented "Not In Service" casing maps to the same option."""
    statuses = {
        f.status
        for f in _parse(Resource.FERRY_TERMINALS).values()
        if isinstance(f, FerryTerminal)
    }
    assert "not_in_service" in statuses


def test_seasonal_load_duplicates_and_whitespace() -> None:
    """Duplicate name and route pairs stay separate; statuses are trimmed."""
    loads = [
        s
        for s in _parse(Resource.SEASONAL_LOADS).values()
        if isinstance(s, SeasonalLoad)
    ]
    raw = load_json_array_fixture("seasonalloadapi.json")
    assert len(loads) == len(raw)
    assert all(load.status == (load.status or "").strip() for load in loads)
    assert {load.restricted for load in loads} == {True, False}


def test_event_activity() -> None:
    """Events are active from their start until their planned end."""
    event = next(
        e for e in _parse(Resource.EVENTS).values() if isinstance(e, TrafficEvent)
    )
    assert event.start is not None
    assert event.planned_end is not None
    assert not event.is_active(event.start - timedelta(seconds=1))
    assert event.is_active(event.start)
    assert event.is_active(event.planned_end)
    assert not event.is_active(event.planned_end + timedelta(seconds=1))


@pytest.mark.parametrize(
    ("condition", "drifting", "visibility", "expected", "surface", "seen"),
    [
        pytest.param(
            "Bare and dry road", "No", "Good", "good", "good", "good", id="clear"
        ),
        # Drifting snow shows in the overall condition; the surface is kept apart.
        pytest.param(
            "Bare and dry road",
            "Yes",
            "Good",
            "drifting_snow",
            "good",
            "good",
            id="drifting",
        ),
        # Worse than drifting snow stays as it is.
        pytest.param("Ice covered", "Yes", "Fair", "icy", "icy", "fair", id="icy"),
        pytest.param(
            "No Report", "Yes", "Poor", "drifting_snow", None, "poor", id="no_report"
        ),
        pytest.param("No Report", None, "Murky", None, None, None, id="unknown"),
    ],
)
def test_road_segment_statuses(
    condition: str,
    drifting: str | None,
    visibility: str,
    expected: str | None,
    surface: str | None,
    seen: str | None,
) -> None:
    """The overall condition includes drifting snow; each part stays available."""
    segment = RoadSegment.from_api(
        road_condition("Test", [(43.7, -79.6), (43.7, -79.5)], condition=condition)
        | {"Drifting": drifting, "Visibility": visibility}
    )
    assert segment.condition.option == expected
    assert segment.surface.option == surface
    assert segment.visibility_option == seen
    assert segment.visibility == visibility


@pytest.mark.parametrize(
    ("status", "option"),
    [
        pytest.param("Open", "open", id="open"),
        pytest.param("Closed", "closed", id="closed"),
        pytest.param("Seasonal", None, id="unrecognised"),
    ],
)
def test_rest_area_status(status: str, option: str | None) -> None:
    """Rest area statuses become options; anything else is Unknown."""
    item = {**load_json_array_fixture("allrestareas.json")[0], "Status": status}
    assert RestArea.from_api(item).status_option == option


@pytest.mark.parametrize(
    ("status", "option"),
    [
        pytest.param("No Restrictions ", "no_restrictions", id="none"),
        pytest.param(
            "Restrictions in effect", "restrictions_in_effect", id="in_effect"
        ),
        pytest.param("Under review", None, id="unrecognised"),
    ],
)
def test_seasonal_load_status(status: str, option: str | None) -> None:
    """Seasonal load statuses become options; anything else is Unknown."""
    item = {**load_json_array_fixture("seasonalloadapi.json")[0], "Status": status}
    assert SeasonalLoad.from_api(item).status_option == option
