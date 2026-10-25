"""Home Assistant glue for the Ontario 511 API client."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util.hass_dict import HassKey

from .api import Ontario511Client, RateLimiter

# One limiter for the whole Home Assistant instance. The API documentation
# doesn't say whether its limit is counted per key or per IP address, so every
# call from this instance shares one budget, whatever key it uses.
_RATE_LIMITER: HassKey[RateLimiter] = HassKey("ontario_511_rate_limiter")


def get_rate_limiter(hass: HomeAssistant) -> RateLimiter:
    """Return the instance-wide rate limiter, creating it on first use."""
    if (limiter := hass.data.get(_RATE_LIMITER)) is None:
        limiter = hass.data[_RATE_LIMITER] = RateLimiter()
    return limiter


def create_client(hass: HomeAssistant, api_key: str) -> Ontario511Client:
    """Create a client that uses the shared session and rate limiter."""
    language = "fr" if hass.config.language.startswith("fr") else "en"
    return Ontario511Client(
        async_get_clientsession(hass), api_key, get_rate_limiter(hass), language
    )
