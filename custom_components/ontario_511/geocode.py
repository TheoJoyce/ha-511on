"""Turning an address into coordinates with OpenStreetMap's Nominatim.

Only used when someone types an address while setting up an area. Follows the
Nominatim usage policy (https://operations.osmfoundation.org/policies/nominatim/):

- Lookups are only ever triggered by the user submitting a form; there's no
  autocomplete and nothing periodic.
- At most one request per second from this Home Assistant instance, and
  identical addresses are answered from a cache.
- Requests identify the integration with its own User-Agent.
- The service can be switched off for every install without a software
  update: before a lookup, a switch file in the integration's repository is
  checked (cached for an hour). If it says off, or can't be read, lookups are
  refused and the area can still be placed on the map.
- The form shows OpenStreetMap attribution and says the address is sent to
  OpenStreetMap.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import os
import time
from typing import Any, Final

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util
from homeassistant.util.hass_dict import HassKey

from .api import REQUEST_TIMEOUT
from .models import in_ontario

SEARCH_URL: Final = "https://nominatim.openstreetmap.org/search"
SWITCH_URL: Final = (
    "https://raw.githubusercontent.com/TheoJoyce/ha-511on/main/geocoding.json"
)
PROJECT_URL: Final = "https://github.com/TheoJoyce/ha-511on"
# For local development only: serve the switch and search from the fake API
# (scripts/fake_511.py) so test addresses never reach OpenStreetMap.
DEV_BASE_ENV: Final = "ONTARIO_511_GEOCODING_BASE"

MIN_INTERVAL_SECONDS: Final = 1.0
SWITCH_CACHE: Final = timedelta(hours=1)

_GEOCODER: HassKey[Geocoder] = HassKey("ontario_511_geocoder")


class GeocodingError(Exception):
    """Base error for address lookups."""


class GeocodingUnavailable(GeocodingError):
    """Lookups are switched off, or the switch couldn't be checked."""


class GeocodingFailed(GeocodingError):
    """The lookup service couldn't be reached."""


class AddressNotFound(GeocodingError):
    """No place matched the address."""


class OutsideOntario(GeocodingError):
    """The address was found, but not in Ontario."""


@dataclass(frozen=True, slots=True)
class Place:
    """A found address."""

    latitude: float
    longitude: float
    name: str


@dataclass
class Geocoder:
    """Rate-limited, cached address lookups shared by every flow."""

    hass: HomeAssistant
    user_agent: str
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _last_request: float = 0.0
    _switch: tuple[datetime, bool] | None = None
    _results: dict[str, Place] = field(default_factory=dict)

    def _urls(self) -> tuple[str, str]:
        if base := os.environ.get(DEV_BASE_ENV):
            return f"{base}/switch.json", f"{base}/search"
        return SWITCH_URL, SEARCH_URL

    async def _get_json(self, url: str, params: dict[str, str] | None = None) -> Any:
        session = async_get_clientsession(self.hass)
        headers = {"User-Agent": self.user_agent, "Referer": PROJECT_URL}
        async with session.get(
            url, params=params, headers=headers, timeout=REQUEST_TIMEOUT
        ) as response:
            response.raise_for_status()
            return await response.json(content_type=None)

    async def _async_enabled(self, switch_url: str) -> bool:
        now = dt_util.utcnow()
        if self._switch is not None and now - self._switch[0] < SWITCH_CACHE:
            return self._switch[1]
        try:
            data = await self._get_json(switch_url)
            enabled = isinstance(data, dict) and data.get("enabled") is True
        except aiohttp.ClientError, TimeoutError, ValueError:
            # Fail closed: without the switch, lookups stay off.
            enabled = False
        self._switch = (now, enabled)
        return enabled

    async def async_lookup(self, address: str) -> Place:
        """Return the place an address refers to, in Ontario."""
        query = " ".join(address.split())
        if (cached := self._results.get(query.lower())) is not None:
            return cached
        switch_url, search_url = self._urls()
        async with self._lock:
            if not await self._async_enabled(switch_url):
                raise GeocodingUnavailable
            wait = self._last_request + MIN_INTERVAL_SECONDS - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()
            try:
                results = await self._get_json(
                    search_url,
                    {
                        "q": query,
                        "format": "jsonv2",
                        "limit": "1",
                        "countrycodes": "ca",
                    },
                )
            except (aiohttp.ClientError, TimeoutError, ValueError) as err:
                raise GeocodingFailed from err
        if not isinstance(results, list) or not results:
            raise AddressNotFound
        first = results[0]
        try:
            place = Place(
                float(first["lat"]),
                float(first["lon"]),
                str(first.get("display_name") or query),
            )
        except (KeyError, TypeError, ValueError) as err:
            raise AddressNotFound from err
        if not in_ontario(place.latitude, place.longitude):
            raise OutsideOntario
        self._results[query.lower()] = place
        return place


def get_geocoder(hass: HomeAssistant, version: str) -> Geocoder:
    """Return the instance-wide geocoder, creating it on first use."""
    if (geocoder := hass.data.get(_GEOCODER)) is None:
        geocoder = hass.data[_GEOCODER] = Geocoder(
            hass, f"ha-511on/{version} (+{PROJECT_URL})"
        )
    return geocoder
