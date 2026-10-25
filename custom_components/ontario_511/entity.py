"""Base entity and shared helpers for Ontario 511."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util
from homeassistant.util.location import distance

from . import Ontario511ConfigEntry
from .const import ATTRIBUTION, CONF_RADIUS, DEFAULT_RADIUS_KM, DOMAIN, SOURCE_URL
from .coordinator import ResourceCoordinator
from .models import TrafficEvent


class Ontario511Entity(CoordinatorEntity[ResourceCoordinator]):
    """An entity backed by an Ontario 511 coordinator."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True

    def __init__(self, coordinator: ResourceCoordinator, key: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=SOURCE_URL,
        )


def nearby_active_events(
    hass: HomeAssistant, entry: Ontario511ConfigEntry
) -> list[tuple[TrafficEvent, float]]:
    """Return active events within the entry's radius of home, nearest first.

    Each item is paired with its distance from home in kilometres.
    """
    radius = entry.options.get(CONF_RADIUS, DEFAULT_RADIUS_KM)
    now = dt_util.utcnow()
    home = (hass.config.latitude, hass.config.longitude)
    found: list[tuple[TrafficEvent, float]] = []
    for event in entry.runtime_data.events.data.values():
        if (
            not isinstance(event, TrafficEvent)
            or event.coordinates is None
            or not event.is_active(now)
        ):
            continue
        metres = distance(*home, *event.coordinates)
        if metres is not None and (km := metres / 1000) <= radius:
            found.append((event, km))
    return sorted(found, key=lambda pair: pair[1])
