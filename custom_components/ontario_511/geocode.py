"""Turning an address into coordinates with OpenStreetMap's Nominatim.

Only used when someone types an address while setting up an area. Follows the
Nominatim usage policy (https://operations.osmfoundation.org/policies/nominatim/):

- Lookups are only ever triggered by the user submitting a form; there's no
  autocomplete and nothing periodic.
- At most one request per second from this Home Assistant instance, and
  identical addresses are answered from a cache.
- The rules shared with routing (User-Agent, remote switch) are in osm.py.
- The form shows OpenStreetMap attribution and says the address is sent to
  OpenStreetMap.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import time
from typing import Final

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.util.hass_dict import HassKey

from .models import in_ontario
from .osm import GEOCODING, Switch, async_get_json, dev_base, get_switch

SEARCH_URL: Final = "https://nominatim.openstreetmap.org/search"

MIN_INTERVAL_SECONDS: Final = 1.0

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
    switch: Switch
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _last_request: float = 0.0
    _results: dict[str, Place] = field(default_factory=dict)

    async def async_lookup(self, address: str) -> Place:
        """Return the place an address refers to, in Ontario."""
        query = " ".join(address.split())
        if (cached := self._results.get(query.lower())) is not None:
            return cached
        search_url = f"{base}/search" if (base := dev_base()) else SEARCH_URL
        async with self._lock:
            if not await self.switch.async_enabled(GEOCODING):
                raise GeocodingUnavailable
            wait = self._last_request + MIN_INTERVAL_SECONDS - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()
            try:
                results = await async_get_json(
                    self.hass,
                    search_url,
                    self.switch.agent,
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
        geocoder = hass.data[_GEOCODER] = Geocoder(hass, get_switch(hass, version))
    return geocoder
