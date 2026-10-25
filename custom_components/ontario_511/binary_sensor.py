"""Binary sensors for Ontario 511."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import Ontario511ConfigEntry
from .entity import Ontario511Entity, nearby_active_events
from .models import Event

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Ontario511ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Ontario 511 binary sensors."""
    async_add_entities(
        [NearbyClosureSensor(entry.runtime_data.events, "nearby_closure")]
    )


class NearbyClosureSensor(Ontario511Entity[Event], BinarySensorEntity):
    """On while an active full road closure is within the configured radius."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    @property
    def is_on(self) -> bool:
        """Return whether any nearby active event closes all lanes."""
        return any(
            event.is_full_closure
            for event, _ in nearby_active_events(
                self.hass, self.coordinator.config_entry
            )
        )
