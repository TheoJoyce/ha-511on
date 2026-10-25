"""Choosing which items an area or commute generates, and suggesting routes.

Pure functions over parsed data, so they can be tested without Home Assistant.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass

from .api import Resource
from .const import DEFAULT_GENERATION_CAP, GENERATION_CAPS
from .geo import Point
from .models import Item, RoadSegment
from .scope import (
    segments_within_corridor,
    segments_within_radius,
    within_corridor,
    within_radius,
)


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
    segment_ids: Sequence[str],
    lines: Sequence[Sequence[Point]],
    corridor_km: float,
    exclude: Collection[str] = (),
) -> Generated:
    """Choose a resource's items for a commute, in route order.

    ``lines`` are the stretches actually driven, so a long segment only
    counts where the trip uses it. The route's own segments always come first
    for road conditions, ahead of other segments that pass through the corridor.
    """
    if resource is Resource.ROAD_CONDITIONS:
        segments = [
            s
            for s in data.values()
            if isinstance(s, RoadSegment) and s.id not in segment_ids
        ]
        nearby = [s for s, _ in segments_within_corridor(segments, lines, corridor_km)]
        ordered: list[Item] = [data[i] for i in segment_ids if i in data] + list(nearby)
        return _take(resource, ordered, exclude)
    return _take(
        resource,
        (item for item, _ in within_corridor(data.values(), lines, corridor_km)),
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
