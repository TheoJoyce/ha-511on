"""Rules shared by the OpenStreetMap services the integration uses.

Address search (Nominatim, ``geocode.py``) and commute routing (OSRM,
``directions.py``) are free services with usage policies. Both:

- are only used when the user submits a form; nothing is periodic;
- identify the integration with its own User-Agent and project link;
- can be switched off for every install without a software update: before a
  request, a switch file in the integration's repository is checked (cached
  for an hour). Each service has its own key in it. If the key isn't true, or
  the file can't be read, the service is refused.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import os
from typing import Any, Final

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util
from homeassistant.util.hass_dict import HassKey

from .api import REQUEST_TIMEOUT

SWITCH_URL: Final = (
    "https://raw.githubusercontent.com/TheoJoyce/ha-511on/main/geocoding.json"
)
PROJECT_URL: Final = "https://github.com/TheoJoyce/ha-511on"
# For local development only: serve the switch, address search and routing
# from the fake API (scripts/fake_511.py) instead of OpenStreetMap.
DEV_BASE_ENV: Final = "ONTARIO_511_OSM_BASE"

SWITCH_CACHE: Final = timedelta(hours=1)
# Switch keys; address search keeps "enabled", which 0.1.0-alpha.1 reads.
GEOCODING: Final = "enabled"
ROUTING: Final = "routing"

_SWITCH: HassKey[Switch] = HassKey("ontario_511_osm_switch")


def dev_base() -> str | None:
    """Return the fake API's base URL in development, else None."""
    return os.environ.get(DEV_BASE_ENV)


def user_agent(version: str) -> str:
    """Return the User-Agent that identifies the integration."""
    return f"ha-511on/{version} (+{PROJECT_URL})"


async def async_get_json(
    hass: HomeAssistant,
    url: str,
    agent: str,
    params: dict[str, str] | None = None,
) -> Any:
    """Fetch JSON from an OpenStreetMap service, identifying the integration."""
    session = async_get_clientsession(hass)
    headers = {"User-Agent": agent, "Referer": PROJECT_URL}
    async with session.get(
        url, params=params, headers=headers, timeout=REQUEST_TIMEOUT
    ) as response:
        response.raise_for_status()
        return await response.json(content_type=None)


@dataclass
class Switch:
    """The remote switch, shared by every service and cached for an hour."""

    hass: HomeAssistant
    agent: str
    _state: tuple[datetime, dict[str, Any]] | None = None

    async def async_enabled(self, key: str) -> bool:
        """Return whether a service is switched on; fail closed."""
        now = dt_util.utcnow()
        if self._state is None or now - self._state[0] >= SWITCH_CACHE:
            url = f"{base}/switch.json" if (base := dev_base()) else SWITCH_URL
            try:
                data = await async_get_json(self.hass, url, self.agent)
            except aiohttp.ClientError, TimeoutError, ValueError:
                data = {}
            self._state = (now, data if isinstance(data, dict) else {})
        return self._state[1].get(key) is True


def get_switch(hass: HomeAssistant, version: str) -> Switch:
    """Return the instance-wide switch, creating it on first use."""
    if (switch := hass.data.get(_SWITCH)) is None:
        switch = hass.data[_SWITCH] = Switch(hass, user_agent(version))
    return switch
