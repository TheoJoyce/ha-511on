"""How fresh each Ontario 511 resource is kept.

This is the single reference for refresh rates; see docs/design.md for how the
numbers were chosen. Changing them must keep the polling budget test passing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Final

from .api import Resource


class Category(StrEnum):
    """How much a change in the data matters."""

    LIVE = "live"
    PERIODIC = "periodic"
    STATIC = "static"


@dataclass(frozen=True, slots=True)
class ResourcePolicy:
    """Refresh rules for one resource.

    ``refresh`` is the background polling interval. ``freshness`` is how long a
    fetched response counts as current: a forced refresh inside that window
    reuses the cached response instead of calling the API.
    """

    category: Category
    refresh: timedelta
    freshness: timedelta

    @property
    def calls_per_minute(self) -> float:
        """Return the background calls per minute this policy costs."""
        return 60 / self.refresh.total_seconds()


_LIVE_FAST = ResourcePolicy(Category.LIVE, timedelta(minutes=3), timedelta(minutes=2))
_STATIC = ResourcePolicy(Category.STATIC, timedelta(hours=24), timedelta(hours=6))
_HOURLY = ResourcePolicy(Category.PERIODIC, timedelta(hours=1), timedelta(minutes=30))

RESOURCE_POLICIES: Final[dict[Resource, ResourcePolicy]] = {
    Resource.EVENTS: _LIVE_FAST,
    Resource.ALERTS: ResourcePolicy(
        Category.LIVE, timedelta(minutes=5), timedelta(minutes=2)
    ),
    Resource.ROAD_CONDITIONS: ResourcePolicy(
        Category.LIVE, timedelta(minutes=10), timedelta(minutes=5)
    ),
    Resource.FERRY_TERMINALS: ResourcePolicy(
        Category.LIVE, timedelta(minutes=10), timedelta(minutes=5)
    ),
    Resource.CONSTRUCTION: ResourcePolicy(
        Category.PERIODIC, timedelta(minutes=30), timedelta(minutes=15)
    ),
    Resource.REST_AREAS: _HOURLY,
    Resource.TRUCK_REST_AREAS: _HOURLY,
    Resource.SERVICE_CENTRES: _HOURLY,
    Resource.SEASONAL_LOADS: ResourcePolicy(
        Category.PERIODIC, timedelta(hours=6), timedelta(hours=1)
    ),
    Resource.CAMERAS: _STATIC,
    Resource.CARPOOL_LOTS: _STATIC,
    Resource.TRANSIT_HUBS: _STATIC,
    Resource.INSPECTION_STATIONS: _STATIC,
    Resource.ROUNDABOUTS: _STATIC,
    Resource.HOV_LANES: _STATIC,
    Resource.INFORMATION_CENTRES: _STATIC,
}

# Background polling with every resource enabled must stay at or under this,
# a tenth of the API's 10 calls per minute.
POLL_BUDGET_PER_MINUTE: Final = 1.0

# Camera images come from the web map, not the API; each view is cached this
# long. The server allows 20 s and images change about every 1 to 2 minutes.
CAMERA_IMAGE_CACHE: Final = timedelta(seconds=60)
