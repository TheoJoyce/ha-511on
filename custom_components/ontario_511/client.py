"""Home Assistant glue for the Ontario 511 API client."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import os
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util.hass_dict import HassKey

from .api import API_BASE, DevCacheClient, Ontario511Client, RateLimiter, Resource
from .models import Item

DEV_API_BASE_ENV = "ONTARIO_511_API_BASE"

# Entering this instead of a real key serves the gitignored full responses in
# a development checkout (.dev-cache/api, next to custom_components/). A normal
# install has no such directory, so there the key only gets an error.
DEV_API_KEY = "dev-cache"
DEV_CACHE_DIR = Path(__file__).resolve().parents[2] / ".dev-cache" / "api"

# One limiter for the whole Home Assistant instance. The API documentation
# doesn't say whether its limit is counted per key or per IP address, so every
# call from this instance shares one budget, whatever key it uses.
RATE_LIMITER: HassKey[RateLimiter] = HassKey("ontario_511_rate_limiter")

# Parsed responses, kept for the lifetime of Home Assistant so reloading a
# config entry (after adding an item or commute) doesn't re-fetch everything.
_RESPONSES: HassKey[dict[tuple[str, Resource], CachedResponse]] = HassKey(
    "ontario_511_responses"
)


@dataclass(frozen=True, slots=True)
class CachedResponse:
    """A parsed response and when it was fetched."""

    fetched: datetime
    items: dict[str, Item]


def key_id(api_key: str) -> str:
    """Return a stable ID for a key without exposing the key."""
    return hashlib.sha256(api_key.encode()).hexdigest()


def get_rate_limiter(hass: HomeAssistant) -> RateLimiter:
    """Return the instance-wide rate limiter, creating it on first use."""
    if (limiter := hass.data.get(RATE_LIMITER)) is None:
        limiter = hass.data[RATE_LIMITER] = RateLimiter()
    return limiter


def response_cache(hass: HomeAssistant) -> dict[tuple[str, Resource], CachedResponse]:
    """Return the instance-wide cache of parsed responses."""
    return hass.data.setdefault(_RESPONSES, {})


def create_client(hass: HomeAssistant, api_key: str) -> Ontario511Client:
    """Create a client that uses the shared session and rate limiter.

    Setting ONTARIO_511_API_BASE points it at a local fake server for
    development (see scripts/fake_511.py), and DEV_API_KEY reads saved
    responses instead. Normal installs use neither.
    """
    if api_key == DEV_API_KEY:
        return DevCacheClient(DEV_CACHE_DIR)
    language = "fr" if hass.config.language.startswith("fr") else "en"
    return Ontario511Client(
        async_get_clientsession(hass),
        api_key,
        get_rate_limiter(hass),
        language,
        os.environ.get(DEV_API_BASE_ENV, API_BASE),
    )
