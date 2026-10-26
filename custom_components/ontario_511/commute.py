"""Planning a commute and reading its stored route.

A commute stores the road segments it uses and the stretches of them actually
driven (``CONF_ROUTE``: pairs of segment ID and encoded polyline). Corridors
follow those stretches, so a long segment only counts where the trip uses it.
Stretches on local roads, from the start to the highway and from the highway
to the end, are straight lines with no segment ID, so the corridor covers the
trip door to door. When local roads beat any highway route, the route is the
straight line from start to end.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any
import uuid

from homeassistant.config_entries import ConfigEntry, ConfigSubentry

from .const import (
    CONF_END,
    CONF_FROM_AREA,
    CONF_ROUTE,
    CONF_SEGMENTS,
    CONF_START,
    CONF_TO_AREA,
    SUBENTRY_AREA,
)
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
        local = ([start, route.entry], [route.exit, end])
        for piece in (
            ["", encode_polyline(local[0])],
            *([sid, encode_polyline(line)] for sid, line in route.pieces),
            ["", encode_polyline(local[1])],
        ):
            if piece not in pieces:
                pieces.append(piece)
    return {CONF_SEGMENTS: ordered, CONF_ROUTE: pieces}


def route_pieces(data: Mapping[str, Any]) -> list[tuple[str, tuple[Point, ...]]]:
    """Return the stretches a commute drives, with their segment IDs.

    Local roads have no segment ID.
    """
    pieces = [
        (segment_id, tuple(decode_polyline(encoded)))
        for segment_id, encoded in data.get(CONF_ROUTE, [])
        if encoded
    ]
    if pieces:
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
    """Return the stretches a commute drives."""
    return [line for _, line in route_pieces(data)]


def change_segments(
    data: Mapping[str, Any], keep: list[str], segments: Mapping[str, RoadSegment]
) -> dict[str, Any]:
    """Return route data using the segments in ``keep``.

    Stretches of kept segments stay as they were; newly added segments are
    used along their full length.
    """
    # Local stretches (no segment ID) stay, so the trip stays door to door.
    pieces = [list(p) for p in data.get(CONF_ROUTE, []) if not p[0] or p[0] in keep]
    for segment_id in keep:
        if segment_id not in data.get(CONF_SEGMENTS, []) and (
            segment := segments.get(segment_id)
        ):
            pieces.extend([segment_id, encode_polyline(line)] for line in segment.lines)
    return {CONF_SEGMENTS: list(keep), CONF_ROUTE: pieces}


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
