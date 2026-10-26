"""Working out which items belong to an area, a commute or a road segment."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from .geo import BoundingBox, Point, distance_km, distance_to_lines_km
from .models import Item, RoadSegment

# Events and construction further than this from every road segment aren't
# attached to one. Live data puts 95% of events within 150 m of a segment.
ATTACH_MAX_KM = 2.0


def within_radius[T: Item](
    items: Iterable[T], centre: Point, radius_km: float
) -> list[tuple[T, float]]:
    """Return items within ``radius_km`` of ``centre``, nearest first."""
    found = [
        (item, km)
        for item in items
        if item.coordinates is not None
        and (km := distance_km(centre, item.coordinates)) <= radius_km
    ]
    return sorted(found, key=lambda pair: pair[1])


def segments_within_radius(
    segments: Iterable[RoadSegment], centre: Point, radius_km: float
) -> list[tuple[RoadSegment, float]]:
    """Return road segments passing within ``radius_km`` of ``centre``."""
    found = [
        (segment, km)
        for segment in segments
        if segment.bbox is not None
        and segment.bbox.may_be_within(centre, radius_km)
        and (km := distance_to_lines_km(centre, segment.lines)) <= radius_km
    ]
    return sorted(found, key=lambda pair: pair[1])


def within_corridor[T: Item](
    items: Iterable[T], lines: Sequence[Sequence[Point]], width_km: float
) -> list[tuple[T, float]]:
    """Return items within ``width_km`` of any of a route's lines.

    Items are paired with their distance from the route and ordered by the
    line they're nearest to, so they follow the route's order.
    """
    boxes = [BoundingBox(line) for line in lines if line]
    found: list[tuple[int, float, T]] = []
    for item in items:
        if item.coordinates is None:
            continue
        best: tuple[int, float] | None = None
        for index, (line, box) in enumerate(zip(lines, boxes, strict=True)):
            if not box.may_be_within(item.coordinates, width_km):
                continue
            km = distance_to_lines_km(item.coordinates, [line])
            if km <= width_km and (best is None or km < best[1]):
                best = (index, km)
        if best is not None:
            found.append((best[0], best[1], item))
    found.sort(key=lambda entry: (entry[0], entry[1]))
    return [(item, km) for _, km, item in found]


def segments_within_corridor(
    segments: Iterable[RoadSegment],
    lines: Sequence[Sequence[Point]],
    width_km: float,
) -> list[tuple[RoadSegment, float]]:
    """Return segments with any part within ``width_km`` of a route's lines.

    Unlike ``within_corridor``, this looks at each segment's whole line rather
    than one representative point, so long parallel roads aren't missed.
    """
    route_lines = [line for line in lines if line]
    boxes = [BoundingBox(line) for line in route_lines]
    found: list[tuple[RoadSegment, float]] = []
    for segment in segments:
        if segment.bbox is None or not any(
            boxes_near(segment.bbox, box, width_km) for box in boxes
        ):
            continue
        km = min(
            (
                distance_to_lines_km(point, route_lines)
                for line in segment.lines
                for point in line
            ),
            default=float("inf"),
        )
        if km <= width_km:
            found.append((segment, km))
    return sorted(found, key=lambda pair: pair[1])


def boxes_near(a: BoundingBox, b: BoundingBox, km: float) -> bool:
    """Return False only when the boxes are certainly further than ``km`` apart."""
    pad = km / 111.0 * 2
    return not (
        a.max_lat + pad < b.min_lat
        or b.max_lat + pad < a.min_lat
        or a.max_lon + pad < b.min_lon
        or b.max_lon + pad < a.min_lon
    )


def nearest_segment(
    point: Point,
    segments: Iterable[RoadSegment],
    max_km: float = ATTACH_MAX_KM,
) -> RoadSegment | None:
    """Return the road segment nearest to ``point``, if one is within ``max_km``."""
    best: tuple[float, RoadSegment] | None = None
    for segment in segments:
        if segment.bbox is None or not segment.bbox.may_be_within(point, max_km):
            continue
        km = distance_to_lines_km(point, segment.lines)
        if km <= max_km and (best is None or km < best[0]):
            best = (km, segment)
    return best[1] if best else None


def attach_to_segments[T: Item](
    items: Iterable[T],
    segments: Sequence[RoadSegment],
    max_km: float = ATTACH_MAX_KM,
) -> dict[str, list[T]]:
    """Group items by the ID of their nearest road segment within ``max_km``."""
    attached: dict[str, list[T]] = {}
    for item in items:
        if item.coordinates is None:
            continue
        if (segment := nearest_segment(item.coordinates, segments, max_km)) is None:
            continue
        attached.setdefault(segment.id, []).append(item)
    return attached
