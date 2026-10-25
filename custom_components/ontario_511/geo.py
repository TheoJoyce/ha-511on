"""Geometry helpers: polylines, distances and corridors.

Distances use an equirectangular projection around each point, which is
accurate to well under 1% at Ontario's latitudes for the short distances
involved here.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
import math

type Point = tuple[float, float]

EARTH_RADIUS_KM = 6371.0


def decode_polyline(encoded: str) -> list[Point]:
    """Decode a Google encoded polyline into (latitude, longitude) points."""
    points: list[Point] = []
    index = lat = lon = 0
    length = len(encoded)
    while index < length:
        deltas = []
        for _ in range(2):
            shift = result = 0
            while True:
                if index >= length:
                    raise ValueError("Truncated polyline")
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            deltas.append(~(result >> 1) if result & 1 else result >> 1)
        lat += deltas[0]
        lon += deltas[1]
        points.append((lat / 1e5, lon / 1e5))
    return points


def distance_km(a: Point, b: Point) -> float:
    """Return the great-circle distance between two points."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, h)))


def _to_xy(point: Point, origin: Point) -> tuple[float, float]:
    scale = math.cos(math.radians(origin[0]))
    return (
        math.radians(point[1] - origin[1]) * scale * EARTH_RADIUS_KM,
        math.radians(point[0] - origin[0]) * EARTH_RADIUS_KM,
    )


def distance_to_line_km(point: Point, line: Sequence[Point]) -> float:
    """Return the shortest distance from a point to a polyline."""
    if not line:
        return math.inf
    if len(line) == 1:
        return distance_km(point, line[0])
    best = math.inf
    prev = _to_xy(line[0], point)
    for vertex in line[1:]:
        cur = _to_xy(vertex, point)
        dx, dy = cur[0] - prev[0], cur[1] - prev[1]
        length_sq = dx * dx + dy * dy
        t = (
            0.0
            if length_sq == 0
            else max(0.0, min(1.0, -(prev[0] * dx + prev[1] * dy) / length_sq))
        )
        best = min(best, math.hypot(prev[0] + t * dx, prev[1] + t * dy))
        prev = cur
    return best


def distance_to_lines_km(point: Point, lines: Iterable[Sequence[Point]]) -> float:
    """Return the shortest distance from a point to any of several polylines."""
    return min((distance_to_line_km(point, line) for line in lines), default=math.inf)


class BoundingBox:
    """A latitude/longitude box, used to skip distant lines cheaply."""

    __slots__ = ("max_lat", "max_lon", "min_lat", "min_lon")

    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float

    def __init__(self, points: Iterable[Point]) -> None:
        """Build the box around the points."""
        lats, lons = zip(*points, strict=True)
        self.min_lat, self.max_lat = min(lats), max(lats)
        self.min_lon, self.max_lon = min(lons), max(lons)

    def may_be_within(self, point: Point, km: float) -> bool:
        """Return False only when the point is certainly further than ``km``."""
        lat_pad = km / 111.0
        lon_pad = km / (111.0 * max(0.1, math.cos(math.radians(point[0]))))
        return (
            self.min_lat - lat_pad <= point[0] <= self.max_lat + lat_pad
            and self.min_lon - lon_pad <= point[1] <= self.max_lon + lon_pad
        )
