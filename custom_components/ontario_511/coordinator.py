"""Data update coordinators for Ontario 511."""

from __future__ import annotations

from collections.abc import Callable
import logging
from typing import TYPE_CHECKING, Any, Protocol

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    Ontario511AuthError,
    Ontario511Client,
    Ontario511Error,
    Ontario511RateLimitError,
    Resource,
)
from .const import DOMAIN, UPDATE_INTERVALS
from .models import Alert, Event

if TYPE_CHECKING:
    from . import Ontario511ConfigEntry

_LOGGER = logging.getLogger(__name__)


class Identified(Protocol):
    """An item keyed by its id."""

    @property
    def id(self) -> str: ...


class Ontario511Coordinator[T: Identified](DataUpdateCoordinator[dict[str, T]]):
    """Poll one Ontario 511 resource on its fixed schedule."""

    config_entry: Ontario511ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: Ontario511ConfigEntry,
        client: Ontario511Client,
        resource: Resource,
        parse: Callable[[dict[str, Any]], T],
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN} {resource.value}",
            update_interval=UPDATE_INTERVALS[resource],
        )
        self.client = client
        self.resource = resource
        self._parse = parse

    async def _async_update_data(self) -> dict[str, T]:
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
        parsed: dict[str, T] = {}
        for item in items:
            try:
                value = self._parse(item)
            except KeyError, TypeError:
                _LOGGER.debug("Skipping malformed %s item", self.resource.value)
                continue
            parsed[value.id] = value
        return parsed


type EventsCoordinator = Ontario511Coordinator[Event]
type AlertsCoordinator = Ontario511Coordinator[Alert]
