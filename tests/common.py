"""Helpers shared by tests."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def encode_polyline(points: Sequence[tuple[float, float]]) -> str:
    """Encode points with Google's polyline algorithm."""
    out, prev = [], (0, 0)
    for lat, lon in points:
        cur = (round(lat * 1e5), round(lon * 1e5))
        for delta in (cur[0] - prev[0], cur[1] - prev[1]):
            value = ~(delta << 1) if delta < 0 else delta << 1
            while value >= 0x20:
                out.append(chr((0x20 | (value & 0x1F)) + 63))
                value >>= 5
            out.append(chr(value + 63))
        prev = cur
    return "".join(out)


def road_condition(
    description: str,
    points: Sequence[tuple[float, float]],
    road: str = "401",
    condition: str = "Bare and dry road",
) -> dict[str, Any]:
    """Return a roadconditions API item for a segment along ``points``."""
    return {
        "LocationDescription": description,
        "Condition": [condition],
        "Visibility": "Good",
        "Drifting": "No",
        "Region": "Central",
        "RoadwayName": road,
        "EncodedPolyline": [encode_polyline(points)],
        "LastUpdated": 1793100000,
    }


# Roughly Highway 401 across north Toronto, split into west and east parts,
# plus a segment far away in Kenora.
WEST_401 = road_condition("West", [(43.715, -79.60), (43.725, -79.50), (43.73, -79.44)])
EAST_401 = road_condition("East", [(43.73, -79.44), (43.745, -79.40), (43.77, -79.25)])
KENORA = road_condition("Kenora", [(49.77, -94.5), (49.78, -94.4)], road="17")
