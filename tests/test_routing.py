"""Tests for planning commutes: along roads, or over the highway network."""

from __future__ import annotations

import itertools
import json
from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from yarl import URL

from custom_components.ontario_511.commute import (
    async_plan_commute,
    change_segments,
    generation_lines,
    generation_segments,
    match_route,
    plan_highway_commute,
    plan_road_commute,
    route_lines,
    route_pieces,
    via_points,
)
from custom_components.ontario_511.const import (
    CONF_ALTERNATIVES,
    CONF_END,
    CONF_ROUTE,
    CONF_ROUTING,
    CONF_SEGMENTS,
    CONF_START,
    ROUTING_HIGHWAYS,
    ROUTING_ROADS,
)
from custom_components.ontario_511.directions import (
    ROUTE_URL,
    RoadRoute,
    async_get_router,
)
from custom_components.ontario_511.geo import (
    decode_polyline,
    distance_km,
    encode_polyline,
)
from custom_components.ontario_511.models import RoadSegment
from custom_components.ontario_511.osm import SWITCH_URL
from custom_components.ontario_511.routing import RoadNetwork

from .common import ALONG_401, EAST_401, KENORA, WEST_401, osrm_response, road_condition
from .conftest import ROUTE_PATTERN

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


def test_plan_highway_commute_and_fallback() -> None:
    """Plans store segments and stretches; local trips fall back to a line."""
    plan = plan_highway_commute([WEST, EAST], START, (43.77, -79.25))
    assert plan[CONF_ROUTING] == ROUTING_HIGHWAYS
    assert plan[CONF_SEGMENTS] == [WEST.id, EAST.id]
    assert plan[CONF_ALTERNATIVES] == []
    assert {sid for sid, _ in plan[CONF_ROUTE]} == {"", WEST.id, EAST.id}
    # Local roads connect the start and end to the highway, so the corridor
    # covers the trip door to door.
    local = [decode_polyline(e) for sid, e in plan[CONF_ROUTE] if not sid]
    assert local[0][0] == START
    assert local[-1][-1] == (43.77, -79.25)

    local = plan_highway_commute([FAR], START, (43.72, -79.55))
    assert local[CONF_SEGMENTS] == []
    data = {
        **local,
        CONF_START: {"latitude": START[0], "longitude": START[1]},
        CONF_END: {"latitude": 43.72, "longitude": -79.55},
    }
    assert route_lines(data) == [(START, (43.72, -79.55))]


def test_change_segments() -> None:
    """Kept segments keep their stretch; added ones count in full."""
    plan = plan_highway_commute([WEST, EAST], START, (43.77, -79.25))
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


# A highway north of and parallel to the 401: a sensible alternative.
NORTH_407_POINTS = [(43.735, -79.60), (43.76, -79.42), (43.785, -79.25)]
NORTH_407 = RoadSegment.from_api(
    road_condition("North 407", NORTH_407_POINTS, road="407")
)
END = (43.77, -79.25)
# Local roads from the end of the 401 to a little north of it.
SPUR = [(43.77, -79.25), (43.80, -79.25)]


def _road(line: list[tuple[float, float]], minutes: float) -> RoadRoute:
    return RoadRoute(tuple(line), 0.0, minutes)


def _ids(pieces: list[tuple[str, tuple[tuple[float, float], ...]]]) -> list[str]:
    return [segment_id for segment_id, _ in pieces]


def test_match_route_along_segments_and_local_roads() -> None:
    """A road route is split where it runs along segments and where it doesn't."""
    pieces = match_route([*ALONG_401, *SPUR[1:]], [WEST, EAST, FAR])
    assert _ids(pieces) == [WEST.id, EAST.id, ""]
    # The pieces join up: each starts where the one before ends.
    for (_, before), (_, after) in itertools.pairwise(pieces):
        assert before[-1] == after[0]
    assert pieces[0][1][0] == ALONG_401[0]
    assert pieces[-1][1][-1] == SPUR[-1]


def test_a_segment_straying_from_the_road_is_still_followed() -> None:
    """A moment where the segment's polyline is far from the road doesn't
    split the route."""
    # About 500 m north of the segment, briefly.
    spike = [(43.7195, -79.553), (43.7245, -79.55), (43.7205, -79.547)]
    road = [ALONG_401[0], *spike, *ALONG_401[1:3]]
    assert _ids(match_route(road, [WEST])) == [WEST.id]


def test_crossing_a_highway_is_not_driving_along_it() -> None:
    """A local road that only crosses a highway doesn't use its segment."""
    across = [(43.70, -79.55), (43.75, -79.55)]
    assert _ids(match_route(across, [WEST, EAST])) == [""]


def test_plan_road_commute_keeps_distinct_reasonable_routes() -> None:
    """The fastest route is the main one; slow and duplicate routes are dropped."""
    along_407 = [ALONG_401[0], *NORTH_407_POINTS, END]
    plan = plan_road_commute(
        [WEST, EAST, NORTH_407],
        [
            _road(along_407, 24),
            _road(ALONG_401, 20),
            # The 401 again, barely different.
            _road([*ALONG_401[:2], (43.7251, -79.4999), *ALONG_401[2:]], 21),
            # Far too slow to be a sensible alternative.
            _road([ALONG_401[0], (44.5, -79.4), END], 60),
        ],
    )
    assert plan[CONF_ROUTING] == ROUTING_ROADS
    assert plan[CONF_SEGMENTS] == [WEST.id, EAST.id]
    data = {**plan, CONF_START: {}, CONF_END: {}}
    assert _ids(route_pieces(data)) == [WEST.id, EAST.id]
    (alternative,) = plan[CONF_ALTERNATIVES]
    assert [sid for sid, _ in alternative] == ["", NORTH_407.id, ""]
    # Items are generated along every route; the corridor is the main one.
    assert generation_segments(data) == [WEST.id, EAST.id, NORTH_407.id]
    assert len(generation_lines(data)) > len(route_lines(data))


def test_via_points_are_on_each_highway_route() -> None:
    """Each highway route gives a point halfway along it."""
    on_401, on_407 = via_points([WEST, EAST, NORTH_407], START, END)
    assert min(distance_km(on_401, p) for p in ALONG_401) < 5
    assert min(distance_km(on_407, p) for p in NORTH_407_POINTS) < 5


async def test_plan_commute_asks_for_missed_highway_routes(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    routing_spacing: AsyncMock,
) -> None:
    """A highway route the service didn't offer is routed through, once."""
    along_407 = [START, *NORTH_407_POINTS, END]

    async def respond(method: str, url: URL, data: object) -> AiohttpClientMockResponse:
        # Three waypoints: through the 407.
        line = along_407 if url.path.count(";") == 2 else ALONG_401
        body = osrm_response([(line, 24.0 if line is along_407 else 20.0)])
        return AiohttpClientMockResponse(method, url, text=json.dumps(body))

    aioclient_mock.get(SWITCH_URL, json={"routing": True})
    aioclient_mock.get(ROUTE_PATTERN, side_effect=respond)
    plan = await async_plan_commute(
        hass, await async_get_router(hass), [WEST, EAST, NORTH_407], START, END
    )
    routes = [c for c in aioclient_mock.mock_calls if str(c[1]).startswith(ROUTE_URL)]
    # The first with the service's alternatives; then only the 407, since the
    # 401's halfway point is already on the route found.
    assert [c[1].query["alternatives"] for c in routes] == ["3", "false"]
    assert routes[1][1].path.count(";") == 2
    # Spaced a second apart, as the routing service's policy asks.
    routing_spacing.assert_awaited_once()
    assert plan[CONF_SEGMENTS] == [WEST.id, EAST.id]
    (alternative,) = plan[CONF_ALTERNATIVES]
    assert NORTH_407.id in {sid for sid, _ in alternative}
