"""Planning a commute and reading its stored route.

A commute has a main route, the one most likely driven, and up to two
alternatives. Each is stored as pieces: pairs of road segment ID and encoded
polyline, with no segment ID for roads Ontario 511 doesn't cover. Items along
any of the routes are generated, but the corridor (the summary's events,
conditions and closures) follows the main route only.

Routes come from OpenStreetMap's routing service and follow real roads
(``CONF_ROUTING`` is ``roads``); the parts that run along an Ontario 511 road
segment carry its ID. Without that service, routes go over the Ontario 511
highway network (``highways``), and the stretches between it and the start
and end are straight lines standing in for local roads.

Commutes planned before 0.1.0-alpha.2 have no ``CONF_ROUTING`` and keep every
route's pieces in ``CONF_ROUTE``; a repair asks to plan them again.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from itertools import pairwise
from typing import Any
import uuid

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_ALTERNATIVES,
    CONF_END,
    CONF_FROM_AREA,
    CONF_ITEMS,
    CONF_LOCATION,
    CONF_PENDING,
    CONF_ROUTE,
    CONF_ROUTING,
    CONF_SEGMENTS,
    CONF_START,
    CONF_TO_AREA,
    ROUTING_HIGHWAYS,
    ROUTING_ROADS,
    SUBENTRY_AREA,
)
from .directions import RoadRoute, Router, RoutingError, async_get_router
from .geo import (
    BoundingBox,
    Point,
    decode_polyline,
    densify,
    distance_km,
    distance_to_line_km,
    encode_polyline,
    line_km,
    simplify,
)
from .models import RoadSegment
from .routing import ALTERNATIVE_FACTOR, MAX_ROUTES, RoadNetwork
from .scope import boxes_near, nearest_segment

# A road route runs along a segment where the segment is this close. Ontario
# 511's polylines are approximate and thinned, so it allows for some offset.
MATCH_KM = 0.3
# Spacing of the points checked against segments.
SAMPLE_KM = 0.2
# Shorter runs along a segment are crossings or ramps, not driving along it.
MIN_RUN_KM = 1.0
# Stored routes drop points that change their shape by less than this.
SIMPLIFY_KM = 0.01
# An alternative that keeps this share of its length on routes already found
# is the same route.
SAME_ROUTE_SHARE = 0.9
SAME_ROUTE_KM = 0.1

type Piece = tuple[str, tuple[Point, ...]]


def _encode(pieces: Iterable[Piece]) -> list[list[str]]:
    return [[segment_id, encode_polyline(line)] for segment_id, line in pieces]


def _decode(stored: Iterable[Sequence[str]]) -> list[Piece]:
    return [
        (segment_id, tuple(decode_polyline(encoded)))
        for segment_id, encoded in stored
        if encoded
    ]


def _segment_ids(pieces: Iterable[Piece]) -> list[str]:
    return list(dict.fromkeys(segment_id for segment_id, _ in pieces if segment_id))


def match_route(line: Sequence[Point], segments: Iterable[RoadSegment]) -> list[Piece]:
    """Split a road route into pieces along Ontario 511 segments and other roads.

    Runs in the executor: it compares many points with many segments.
    """
    box = BoundingBox(line)
    nearby = [s for s in segments if s.bbox and boxes_near(s.bbox, box, MATCH_KM)]
    runs: list[tuple[str, list[Point]]] = []
    for point in densify(line, SAMPLE_KM):
        segment = nearest_segment(point, nearby, MATCH_KM)
        label = segment.id if segment else ""
        if runs and runs[-1][0] == label:
            runs[-1][1].append(point)
            continue
        if runs:
            # Pieces share their boundary point, so the route stays connected.
            runs[-1][1].append(point)
        runs.append((label, [point]))
    merged: list[tuple[str, list[Point]]] = []
    for index, (label, points) in enumerate(runs):
        short = line_km(points) < MIN_RUN_KM
        if label and short:
            label = ""
        elif (
            not label
            and short
            and 0 < index < len(runs) - 1
            and runs[index - 1][0] == runs[index + 1][0]
        ):
            # Where a segment's polyline strays from the road for a moment.
            label = runs[index - 1][0]
        if merged and merged[-1][0] == label:
            merged[-1][1].extend(points[1:])
        else:
            merged.append((label, points))
    return [
        (label, tuple(simplify(points, SIMPLIFY_KM)))
        for label, points in merged
        if len(points) > 1
    ]


def _same_route(route: RoadRoute, others: Iterable[RoadRoute]) -> bool:
    points = densify(route.line, 1.0)
    shared = sum(
        1
        for point in points
        if any(distance_to_line_km(point, o.line) <= SAME_ROUTE_KM for o in others)
    )
    return shared >= SAME_ROUTE_SHARE * len(points)


def plan_road_commute(
    segments: Iterable[RoadSegment], roads: Sequence[RoadRoute]
) -> dict[str, Any]:
    """Return a commute's routes from road routes, fastest first.

    Keeps up to three distinct routes within ALTERNATIVE_FACTOR of the
    fastest's driving time. Runs in the executor.
    """
    ordered = sorted(roads, key=lambda road: road.minutes)
    chosen = ordered[:1]
    for road in ordered[1:]:
        if len(chosen) >= MAX_ROUTES:
            break
        if road.minutes <= chosen[0].minutes * ALTERNATIVE_FACTOR and not _same_route(
            road, chosen
        ):
            chosen.append(road)
    segments = list(segments)
    main, *alternatives = (match_route(road.line, segments) for road in chosen)
    return {
        CONF_ROUTING: ROUTING_ROADS,
        CONF_ROUTE: _encode(main),
        CONF_ALTERNATIVES: [_encode(pieces) for pieces in alternatives],
        CONF_SEGMENTS: _segment_ids(main),
    }


def plan_highway_commute(
    segments: Iterable[RoadSegment], start: Point, end: Point
) -> dict[str, Any]:
    """Return a commute's routes over the Ontario 511 highway network.

    Used when road routing isn't available. Runs in the executor.
    """
    routes = RoadNetwork(segments).routes(start, end)
    planned = [
        [
            ("", (start, route.entry)),
            *route.pieces,
            ("", (route.exit, end)),
        ]
        for route in routes
    ] or [[("", (start, end))]]
    main, *alternatives = planned
    return {
        CONF_ROUTING: ROUTING_HIGHWAYS,
        CONF_ROUTE: _encode(main),
        # Their straight stand-ins for local roads would only add clutter.
        CONF_ALTERNATIVES: [
            _encode(p for p in pieces if p[0]) for pieces in alternatives
        ],
        CONF_SEGMENTS: _segment_ids(main),
    }


def _point_along(line: Sequence[Point], km: float) -> Point:
    for a, b in pairwise(line):
        step = distance_km(a, b)
        if step >= km and step > 0:
            t = km / step
            return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        km -= step
    return line[-1]


def via_points(
    segments: Iterable[RoadSegment], start: Point, end: Point
) -> list[Point]:
    """Return a point halfway along each highway route between start and end.

    Routing service alternatives often miss a sensible highway route; asking
    for a route through these points finds it. Runs in the executor.
    """
    vias = []
    for route in RoadNetwork(segments).routes(start, end):
        line = [point for _, piece in route.pieces for point in piece]
        if len(line) > 1:
            vias.append(_point_along(line, line_km(line) / 2))
    return vias


async def async_plan_commute(
    hass: HomeAssistant,
    router: Router,
    segments: Sequence[RoadSegment],
    start: Point,
    end: Point,
) -> dict[str, Any]:
    """Return a commute's routes along roads.

    One request for the fastest route and the service's alternatives, then at
    most two more for highway routes it missed. Raises RoutingError if the
    first request fails.
    """
    roads = list(await router.async_routes([start, end], alternatives=True))
    if len(roads) < MAX_ROUTES:
        for via in await hass.async_add_executor_job(via_points, segments, start, end):
            if len(roads) >= MAX_ROUTES:
                break
            if any(distance_to_line_km(via, road.line) <= MATCH_KM for road in roads):
                continue
            try:
                roads.extend((await router.async_routes([start, via, end]))[:1])
            except RoutingError:
                break
    return await hass.async_add_executor_job(plan_road_commute, segments, roads)


def route_pieces(data: Mapping[str, Any]) -> list[Piece]:
    """Return the main route's pieces; local roads have no segment ID."""
    if pieces := _decode(data.get(CONF_ROUTE, [])):
        return pieces
    start, end = data[CONF_START], data[CONF_END]
    return [
        (
            "",
            (
                (start["latitude"], start["longitude"]),
                (end["latitude"], end["longitude"]),
            ),
        )
    ]


def route_lines(data: Mapping[str, Any]) -> list[tuple[Point, ...]]:
    """Return the main route's lines: the commute's corridor."""
    return [line for _, line in route_pieces(data)]


def alternative_pieces(data: Mapping[str, Any]) -> list[list[Piece]]:
    """Return each alternative route's pieces."""
    return [_decode(pieces) for pieces in data.get(CONF_ALTERNATIVES, [])]


def generation_lines(data: Mapping[str, Any]) -> list[tuple[Point, ...]]:
    """Return the lines of every route, which items are generated along."""
    return route_lines(data) + [
        line for pieces in alternative_pieces(data) for _, line in pieces
    ]


def generation_segments(data: Mapping[str, Any]) -> list[str]:
    """Return the segments of every route, the main route's first."""
    ids = [
        *data.get(CONF_SEGMENTS, []),
        *(sid for pieces in alternative_pieces(data) for sid, _ in pieces),
    ]
    return [sid for sid in dict.fromkeys(ids) if sid]


def change_segments(
    data: Mapping[str, Any], keep: list[str], segments: Mapping[str, RoadSegment]
) -> dict[str, Any]:
    """Return route data with the main route using the segments in ``keep``.

    Stretches of kept segments stay as they were; newly added segments are
    used along their full length.
    """
    # Pieces without a segment ID stay, so the trip stays door to door.
    pieces = [list(p) for p in data.get(CONF_ROUTE, []) if not p[0] or p[0] in keep]
    for segment_id in keep:
        if segment_id not in data.get(CONF_SEGMENTS, []) and (
            segment := segments.get(segment_id)
        ):
            pieces.extend([segment_id, encode_polyline(line)] for line in segment.lines)
    return {CONF_SEGMENTS: list(keep), CONF_ROUTE: pieces}


def commute_endpoints(
    entry: ConfigEntry, commute: ConfigSubentry
) -> tuple[Point, Point]:
    """Return where a commute starts and ends.

    The centres of its two areas, which may have moved since it was planned;
    the stored start and end if either area is gone.
    """
    areas = linked_areas(entry, commute)
    if len(areas) == 2:
        start, end = (a.data[CONF_LOCATION] for a in areas)
    else:
        start, end = commute.data[CONF_START], commute.data[CONF_END]
    return (start["latitude"], start["longitude"]), (end["latitude"], end["longitude"])


def replanned(
    data: Mapping[str, Any], plan: Mapping[str, Any], start: Point, end: Point
) -> dict[str, Any]:
    """Return a commute's data with new routes.

    The types it has items of are generated again, which adds items along the
    new routes; existing items stay.
    """
    return {
        **data,
        **plan,
        CONF_START: {"latitude": start[0], "longitude": start[1]},
        CONF_END: {"latitude": end[0], "longitude": end[1]},
        CONF_PENDING: sorted({*data.get(CONF_PENDING, []), *data.get(CONF_ITEMS, {})}),
    }


async def async_replan(
    hass: HomeAssistant,
    entry: ConfigEntry,
    commute: ConfigSubentry,
    segments: Sequence[RoadSegment],
    data: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Plan a commute's routes along roads again, from its areas' centres.

    ``data`` is the commute's data to update, if not its stored data. Raises
    RoutingError if routing isn't available.
    """
    start, end = commute_endpoints(entry, commute)
    plan = await async_plan_commute(
        hass, await async_get_router(hass), segments, start, end
    )
    return replanned(commute.data if data is None else data, plan, start, end)


def new_area_key() -> str:
    """Return a new link key for an area, stored as its subentry unique ID.

    Commutes reference areas by this key. Initial setup creates areas and a
    commute together, before Home Assistant assigns subentry IDs, so the key
    is chosen here.
    """
    return f"area_{uuid.uuid4().hex}"


def area_key(subentry: ConfigSubentry) -> str:
    """Return an area's link key (its subentry ID for areas without one)."""
    return subentry.unique_id or subentry.subentry_id


def linked_areas(entry: ConfigEntry, commute: ConfigSubentry) -> list[ConfigSubentry]:
    """Return the areas a commute runs between that still exist, from then to."""
    areas = {
        area_key(s): s
        for s in entry.subentries.values()
        if s.subentry_type == SUBENTRY_AREA
    }
    keys = (commute.data.get(CONF_FROM_AREA), commute.data.get(CONF_TO_AREA))
    return [areas[k] for k in keys if k in areas]
