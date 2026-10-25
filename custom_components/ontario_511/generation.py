"""Choosing which items an area or commute generates, and suggesting routes.

Pure functions over parsed data, so they can be tested without Home Assistant.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
import math

from .api import Resource
from .const import DEFAULT_GENERATION_CAP, GENERATION_CAPS
from .geo import Point, distance_km, distance_to_line_km
from .models import Item, RoadSegment
from .scope import (
    segments_within_corridor,
    segments_within_radius,
    within_corridor,
    within_radius,
)

# How far either side of the straight start-to-end line road segments are
# suggested for a commute, as a share of the trip length, within limits.
SUGGESTION_SHARE = 0.15
SUGGESTION_MIN_KM = 5.0
SUGGESTION_MAX_KM = 25.0
# Share of a segment that must lie in that band for it to be ticked by default.
LIKELY_SHARE = 0.5


def cap_for(resource: Resource) -> int:
    """Return the initial generation cap for a resource."""
    return GENERATION_CAPS.get(resource, DEFAULT_GENERATION_CAP)


@dataclass(frozen=True, slots=True)
class Generated:
    """Items chosen for one resource, as {item ID: display name}."""

    resource: Resource
    items: dict[str, str]


def generate_for_area(
    resource: Resource,
    data: Mapping[str, Item],
    centre: Point,
    radius_km: float,
    exclude: Collection[str] = (),
) -> Generated:
    """Choose a resource's items for an area: nearest to the centre first."""
    candidates: Iterable[tuple[Item, float]]
    if resource is Resource.ROAD_CONDITIONS:
        segments = [s for s in data.values() if isinstance(s, RoadSegment)]
        candidates = segments_within_radius(segments, centre, radius_km)
    else:
        candidates = within_radius(data.values(), centre, radius_km)
    return _take(resource, (item for item, _ in candidates), exclude)


def generate_for_commute(
    resource: Resource,
    data: Mapping[str, Item],
    route: Sequence[RoadSegment],
    corridor_km: float,
    exclude: Collection[str] = (),
) -> Generated:
    """Choose a resource's items for a commute, in route order.

    The route's own segments always come first for road conditions, ahead of
    other segments that merely pass through the corridor.
    """
    if resource is Resource.ROAD_CONDITIONS:
        route_ids = [s.id for s in route]
        segments = [
            s
            for s in data.values()
            if isinstance(s, RoadSegment) and s.id not in route_ids
        ]
        nearby = [s for s, _ in segments_within_corridor(segments, route, corridor_km)]
        ordered: list[Item] = [data[i] for i in route_ids if i in data] + list(nearby)
        return _take(resource, ordered, exclude)
    return _take(
        resource,
        (item for item, _ in within_corridor(data.values(), route, corridor_km)),
        exclude,
    )


def _take(
    resource: Resource, items: Iterable[Item], exclude: Collection[str]
) -> Generated:
    chosen: dict[str, str] = {}
    cap = cap_for(resource)
    for item in items:
        if item.id in exclude or item.id in chosen:
            continue
        chosen[item.id] = item.name
        if len(chosen) >= cap:
            break
    return Generated(resource, chosen)


@dataclass(frozen=True, slots=True)
class Suggestion:
    """A road segment a commute might use."""

    segment: RoadSegment
    # Most of the segment runs along the trip, so it's ticked by default.
    likely: bool


def suggest_route(
    segments: Iterable[RoadSegment], start: Point, end: Point
) -> list[Suggestion]:
    """Suggest the road segments a commute between two points might use.

    Without a routing engine, this looks at segments near the straight line
    from start to end. Segments mostly inside that band are marked likely;
    ones that only touch it are offered too. The user confirms the list.
    """
    length = distance_km(start, end)
    buffer = min(SUGGESTION_MAX_KM, max(SUGGESTION_MIN_KM, length * SUGGESTION_SHARE))
    line = [start, end]
    found: list[tuple[float, Suggestion]] = []
    for segment in segments:
        if segment.bbox is None:
            continue
        if not (
            segment.bbox.may_be_within(start, length + buffer)
            and segment.bbox.may_be_within(end, length + buffer)
        ):
            continue
        points = [p for seg_line in segment.lines for p in seg_line]
        near = [p for p in points if distance_to_line_km(p, line) <= buffer]
        if not near:
            continue
        progress = sum(_progress(p, start, end) for p in near) / len(near)
        likely = len(near) >= LIKELY_SHARE * len(points)
        found.append((progress, Suggestion(segment, likely)))
    found.sort(key=lambda entry: entry[0])
    return [suggestion for _, suggestion in found]


def _progress(point: Point, start: Point, end: Point) -> float:
    """Return how far along the start-to-end line a point projects, 0 to 1."""
    scale = math.cos(math.radians(start[0]))
    ax, ay = (end[1] - start[1]) * scale, end[0] - start[0]
    px, py = (point[1] - start[1]) * scale, point[0] - start[0]
    length_sq = ax * ax + ay * ay
    if length_sq == 0:
        return 0.0
    return max(0.0, min(1.0, (px * ax + py * ay) / length_sq))
