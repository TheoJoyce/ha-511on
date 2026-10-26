"""Tests for routing commutes over the road segment network."""

from __future__ import annotations

import itertools

from custom_components.ontario_511.commute import (
    change_segments,
    plan_commute,
    route_lines,
)
from custom_components.ontario_511.const import (
    CONF_END,
    CONF_ROUTE,
    CONF_SEGMENTS,
    CONF_START,
)
from custom_components.ontario_511.geo import (
    decode_polyline,
    distance_km,
    encode_polyline,
)
from custom_components.ontario_511.models import RoadSegment
from custom_components.ontario_511.routing import RoadNetwork

from .common import EAST_401, KENORA, WEST_401, road_condition

WEST = RoadSegment.from_api(WEST_401)
EAST = RoadSegment.from_api(EAST_401)
FAR = RoadSegment.from_api(KENORA)
# Joins the 401 partway along WEST (a T-junction), heading north.
NORTH = RoadSegment.from_api(
    road_condition(
        "North", [(43.725, -79.50), (43.80, -79.51), (43.90, -79.52)], road="400"
    )
)
START = (43.715, -79.60)


def test_encode_round_trip() -> None:
    """Encoding then decoding gives the same points (to 5 decimal places)."""
    points = [(43.715, -79.6), (43.725, -79.5), (43.73, -79.44)]
    assert decode_polyline(encode_polyline(points)) == points


def test_route_along_connected_segments() -> None:
    """A trip along the 401 uses both segments, in travel order."""
    routes = RoadNetwork([FAR, EAST, WEST]).routes(START, (43.77, -79.25))
    assert routes
    assert routes[0].segments == (WEST.id, EAST.id)


def test_t_junction_route_uses_only_the_driven_stretch() -> None:
    """Turning north at a T-junction uses only part of the segment it leaves."""
    routes = RoadNetwork([WEST, EAST, NORTH]).routes(START, (43.90, -79.52))
    assert routes[0].segments == (WEST.id, NORTH.id)
    west_piece = next(line for sid, line in routes[0].pieces if sid == WEST.id)
    # The driven part of WEST ends at the junction, not at WEST's far end.
    assert distance_km(west_piece[-1], (43.725, -79.50)) < 1.5
    assert distance_km(west_piece[-1], WEST.lines[0][-1]) > 3


def test_local_trip_has_no_highway_route() -> None:
    """When local roads beat any highway route, there's no route."""
    assert RoadNetwork([FAR]).routes(START, (43.72, -79.55)) == []


def test_plan_commute_and_fallback() -> None:
    """Plans store segments and stretches; local trips fall back to a line."""
    plan = plan_commute([WEST, EAST], START, (43.77, -79.25))
    assert plan[CONF_SEGMENTS] == [WEST.id, EAST.id]
    assert {sid for sid, _ in plan[CONF_ROUTE]} == {"", WEST.id, EAST.id}
    # Local roads connect the start and end to the highway, so the corridor
    # covers the trip door to door.
    local = [decode_polyline(e) for sid, e in plan[CONF_ROUTE] if not sid]
    assert local[0][0] == START
    assert local[-1][-1] == (43.77, -79.25)

    local = plan_commute([FAR], START, (43.72, -79.55))
    assert local[CONF_SEGMENTS] == []
    data = {
        **local,
        CONF_START: {"latitude": START[0], "longitude": START[1]},
        CONF_END: {"latitude": 43.72, "longitude": -79.55},
    }
    assert route_lines(data) == [(START, (43.72, -79.55))]


def test_change_segments() -> None:
    """Kept segments keep their stretch; added ones count in full."""
    plan = plan_commute([WEST, EAST], START, (43.77, -79.25))
    changed = change_segments(plan, [WEST.id, NORTH.id], {NORTH.id: NORTH})
    assert changed[CONF_SEGMENTS] == [WEST.id, NORTH.id]
    pieces = {sid for sid, _ in changed[CONF_ROUTE]}
    assert pieces == {"", WEST.id, NORTH.id}
    north_line = next(
        decode_polyline(encoded)
        for sid, encoded in changed[CONF_ROUTE]
        if sid == NORTH.id
    )
    assert len(north_line) == len(NORTH.lines[0])


# Highway 401 across Toronto as one long segment, with no junction between
# its ends (like the QEW between Highway 427 and Mississauga).
# Points about every 800 m, as real segments have (thinned to about 250 m).
LONG_401 = RoadSegment.from_api(
    road_condition(
        "Long 401", [(43.70, round(-79.80 + i * 0.01, 2)) for i in range(61)]
    )
)


def test_joins_and_leaves_between_junctions() -> None:
    """Routes use any point of a segment, as drivers use any interchange."""
    start, end = (43.71, -79.70), (43.71, -79.30)
    (route,) = RoadNetwork([LONG_401]).routes(start, end)
    assert route.segments == (LONG_401.id,)
    assert distance_km(route.entry, (43.70, -79.70)) < 1.5
    assert distance_km(route.exit, (43.70, -79.30)) < 1.5
    ((_, piece),) = route.pieces
    # The driven stretch is the part between the two, not the whole segment.
    assert 30 < sum(distance_km(a, b) for a, b in itertools.pairwise(piece)) < 35


def test_short_highway_hop_is_not_a_route() -> None:
    """A highway that barely helps doesn't beat local roads."""
    assert RoadNetwork([LONG_401]).routes((43.75, -79.70), (43.75, -79.65)) == []
