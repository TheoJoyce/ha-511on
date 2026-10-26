"""Road routes for commutes from OpenStreetMap's routing service (OSRM).

Ontario 511 only publishes provincial highways, so on its own a commute can't
follow local roads. The FOSSGIS OSRM server (https://routing.openstreetmap.de)
routes over every road. Its usage policy allows light use: at most one request
per second, a valid User-Agent, attribution, no scraping or heavy use. So:

- Routes are only requested when the user submits a commute form or repair;
  never in the background.
- At most one request per second from this Home Assistant instance, and
  identical requests are answered from a cache.
- The rules shared with address search (User-Agent, remote switch) are in
  osm.py; the switch's ``routing`` key turns routing off for every install.
- The form shows OpenStreetMap attribution and says the start and end are
  sent to OpenStreetMap.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
import time
from typing import Final

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration
from homeassistant.util.hass_dict import HassKey

from .const import DOMAIN
from .geo import Point, decode_polyline
from .osm import ROUTING, Switch, async_get_json, dev_base, get_switch

ROUTE_URL: Final = "https://routing.openstreetmap.de/routed-car/route/v1/driving"
MIN_INTERVAL_SECONDS: Final = 1.0

_ROUTER: HassKey[Router] = HassKey("ontario_511_router")


class RoutingError(Exception):
    """Base error for routing."""


class RoutingUnavailable(RoutingError):
    """Routing is switched off, or the switch couldn't be checked."""


class RoutingFailed(RoutingError):
    """The routing service couldn't be reached or found no route."""


@dataclass(frozen=True, slots=True)
class RoadRoute:
    """A route along roads, with its driving distance and time."""

    line: tuple[Point, ...]
    km: float
    minutes: float


@dataclass
class Router:
    """Rate-limited, cached routing shared by every flow."""

    hass: HomeAssistant
    switch: Switch
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _last_request: float = 0.0
    _results: dict[str, list[RoadRoute]] = field(default_factory=dict)

    async def async_routes(
        self, waypoints: Sequence[Point], *, alternatives: bool = False
    ) -> list[RoadRoute]:
        """Return routes through the waypoints, fastest first."""
        # OSRM takes longitude first.
        coordinates = ";".join(f"{lon:.5f},{lat:.5f}" for lat, lon in waypoints)
        key = f"{coordinates}|{alternatives}"
        if (cached := self._results.get(key)) is not None:
            return cached
        url = f"{base}/route" if (base := dev_base()) else ROUTE_URL
        async with self._lock:
            if not await self.switch.async_enabled(ROUTING):
                raise RoutingUnavailable
            wait = self._last_request + MIN_INTERVAL_SECONDS - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()
            try:
                data = await async_get_json(
                    self.hass,
                    f"{url}/{coordinates}",
                    self.switch.agent,
                    {
                        "overview": "full",
                        "geometries": "polyline",
                        "steps": "false",
                        "alternatives": "3" if alternatives else "false",
                    },
                )
            except (aiohttp.ClientError, TimeoutError, ValueError) as err:
                raise RoutingFailed from err
        try:
            routes = [
                RoadRoute(
                    tuple(decode_polyline(route["geometry"])),
                    float(route["distance"]) / 1000,
                    float(route["duration"]) / 60,
                )
                for route in data["routes"]
            ]
        except (KeyError, TypeError, ValueError) as err:
            raise RoutingFailed from err
        if not routes or any(len(route.line) < 2 for route in routes):
            raise RoutingFailed
        routes.sort(key=lambda route: route.minutes)
        self._results[key] = routes
        return routes


async def async_get_router(hass: HomeAssistant) -> Router:
    """Return the instance-wide router, creating it on first use."""
    if (router := hass.data.get(_ROUTER)) is None:
        integration = await async_get_integration(hass, DOMAIN)
        router = hass.data[_ROUTER] = Router(
            hass, get_switch(hass, str(integration.version))
        )
    return router
