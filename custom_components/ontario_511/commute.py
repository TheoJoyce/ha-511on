"""Planning a commute and reading its stored route.

A commute stores the road segments it uses and the stretches of them actually
driven (``CONF_ROUTE``: pairs of segment ID and encoded polyline). Corridors
follow those stretches, so a long segment only counts where the trip uses it.
When local roads beat any highway route, the route is the straight line from
start to end, with no segment.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .const import CONF_END, CONF_ROUTE, CONF_SEGMENTS, CONF_START
from .geo import Point, decode_polyline, encode_polyline
from .models import RoadSegment
from .routing import RoadNetwork


def plan_commute(
    segments: Iterable[RoadSegment], start: Point, end: Point
) -> dict[str, Any]:
    """Return the segments and route pieces for a commute.

    Runs routing, so call it in the executor.
    """
    routes = RoadNetwork(segments).routes(start, end)
    if not routes:
        return {CONF_SEGMENTS: [], CONF_ROUTE: [["", encode_polyline([start, end])]]}
    ordered: list[str] = []
    pieces: list[list[str]] = []
    for route in routes:
        for segment_id in route.segments:
            if segment_id not in ordered:
                ordered.append(segment_id)
        pieces.extend([sid, encode_polyline(line)] for sid, line in route.pieces)
    return {CONF_SEGMENTS: ordered, CONF_ROUTE: pieces}


def route_lines(data: Mapping[str, Any]) -> list[tuple[Point, ...]]:
    """Return the stretches a commute drives."""
    lines = [
        tuple(decode_polyline(encoded))
        for _, encoded in data.get(CONF_ROUTE, [])
        if encoded
    ]
    if lines:
        return lines
    start, end = data[CONF_START], data[CONF_END]
    return [
        (
            (start["latitude"], start["longitude"]),
            (end["latitude"], end["longitude"]),
        )
    ]


def change_segments(
    data: Mapping[str, Any], keep: list[str], segments: Mapping[str, RoadSegment]
) -> dict[str, Any]:
    """Return route data using the segments in ``keep``.

    Stretches of kept segments stay as they were; newly added segments are
    used along their full length.
    """
    pieces = [list(p) for p in data.get(CONF_ROUTE, []) if p[0] in keep]
    for segment_id in keep:
        if segment_id not in data.get(CONF_SEGMENTS, []) and (
            segment := segments.get(segment_id)
        ):
            pieces.extend([segment_id, encode_polyline(line)] for line in segment.lines)
    return {CONF_SEGMENTS: list(keep), CONF_ROUTE: pieces}
