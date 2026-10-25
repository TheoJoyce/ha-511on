"""The Ontario 511 integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.core import HomeAssistant

from .api import Resource
from .client import create_client
from .coordinator import AlertsCoordinator, EventsCoordinator, Ontario511Coordinator
from .models import Alert, Event

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.GEO_LOCATION,
    Platform.SENSOR,
]


@dataclass
class Ontario511Data:
    """Runtime data for a config entry."""

    events: EventsCoordinator
    alerts: AlertsCoordinator


type Ontario511ConfigEntry = ConfigEntry[Ontario511Data]


async def async_setup_entry(hass: HomeAssistant, entry: Ontario511ConfigEntry) -> bool:
    """Set up Ontario 511 from a config entry."""
    client = create_client(hass, entry.data[CONF_API_KEY])
    events = Ontario511Coordinator(hass, entry, client, Resource.EVENTS, Event.from_api)
    alerts = Ontario511Coordinator(hass, entry, client, Resource.ALERTS, Alert.from_api)
    await events.async_config_entry_first_refresh()
    await alerts.async_config_entry_first_refresh()
    entry.runtime_data = Ontario511Data(events=events, alerts=alerts)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: Ontario511ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
