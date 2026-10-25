"""Sensors for Ontario 511."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import Ontario511ConfigEntry
from .entity import Ontario511Entity, nearby_active_events
from .models import Alert

PARALLEL_UPDATES = 0

MAX_LISTED_ALERTS = 10


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Ontario511ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Ontario 511 sensors."""
    data = entry.runtime_data
    async_add_entities(
        [
            NearbyEventsSensor(data.events, "nearby_events"),
            ActiveAlertsSensor(data.alerts, "active_alerts"),
        ]
    )


class NearbyEventsSensor(Ontario511Entity, SensorEntity):
    """Number of active events within the configured radius of home."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int:
        """Return the number of nearby active events."""
        return len(nearby_active_events(self.hass, self.coordinator.config_entry))


class ActiveAlertsSensor(Ontario511Entity, SensorEntity):
    """Number of province-wide alerts currently published."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _unrecorded_attributes = frozenset({"alerts"})

    @property
    def native_value(self) -> int:
        """Return the number of alerts."""
        return len(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the alert messages, high importance first."""
        alerts = sorted(
            (a for a in self.coordinator.data.values() if isinstance(a, Alert)),
            key=lambda alert: not alert.high_importance,
        )
        return {
            "alerts": [
                {
                    "message": alert.message,
                    "regions": list(alert.regions),
                    "high_importance": alert.high_importance,
                }
                for alert in alerts[:MAX_LISTED_ALERTS]
            ]
        }
