"""Cached, rate-limited polling of Ontario 511 resources."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    Ontario511AuthError,
    Ontario511Client,
    Ontario511Error,
    Ontario511RateLimitError,
    Resource,
)
from .const import DOMAIN
from .models import PARSERS, Item
from .policy import RESOURCE_POLICIES

if TYPE_CHECKING:
    from . import Ontario511ConfigEntry

_LOGGER = logging.getLogger(__name__)

# Forced refreshes arriving within this many seconds are merged into one.
REQUEST_REFRESH_COOLDOWN = 10.0


def parse_items(resource: Resource, items: list[dict[str, Any]]) -> dict[str, Item]:
    """Parse API items into models keyed by ID, skipping malformed ones."""
    parse = PARSERS[resource]
    parsed: dict[str, Item] = {}
    for item in items:
        try:
            value = parse(item)
        except KeyError, TypeError, ValueError:
            _LOGGER.debug("Skipping malformed %s item", resource.value)
            continue
        parsed[value.id] = value
    return parsed


class ResourceCoordinator(DataUpdateCoordinator[dict[str, Item]]):
    """Poll one resource on its policy's schedule and cache the whole response.

    Every entity of the resource reads from this cache. A forced refresh (the
    "Update entity" action) only calls the API when the cached response is
    older than the resource's freshness window.
    """

    config_entry: Ontario511ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: Ontario511ConfigEntry,
        client: Ontario511Client,
        resource: Resource,
    ) -> None:
        """Initialize the coordinator."""
        self.policy = RESOURCE_POLICIES[resource]
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN} {resource.value}",
            update_interval=self.policy.refresh,
            request_refresh_debouncer=Debouncer(
                hass, _LOGGER, cooldown=REQUEST_REFRESH_COOLDOWN, immediate=True
            ),
        )
        self.client = client
        self.resource = resource
        self.last_fetched: datetime | None = None

    @property
    def is_fresh(self) -> bool:
        """Return whether the cached response is within its freshness window."""
        return (
            self.last_update_success
            and self.last_fetched is not None
            and dt_util.utcnow() - self.last_fetched < self.policy.freshness
        )

    async def async_request_refresh(self) -> None:
        """Refresh on request, reusing the cache while it's fresh."""
        if self.is_fresh:
            self.async_update_listeners()
            return
        await super().async_request_refresh()

    async def _async_update_data(self) -> dict[str, Item]:
        try:
            items = await self.client.async_get(self.resource)
        except Ontario511AuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from err
        except Ontario511RateLimitError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN, translation_key="rate_limited"
            ) from err
        except Ontario511Error as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err
        # Decoding road polylines takes a noticeable time on small hardware.
        parsed = await self.hass.async_add_executor_job(
            parse_items, self.resource, items
        )
        self.last_fetched = dt_util.utcnow()
        return parsed
