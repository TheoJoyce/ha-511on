"""Geolocation events for nearby Ontario 511 traffic events."""

from __future__ import annotations

from typing import Any

from homeassistant.components.geo_location import GeolocationEvent
from homeassistant.const import UnitOfLength
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import Ontario511ConfigEntry
from .const import ATTRIBUTION, DOMAIN
from .coordinator import EventsCoordinator
from .entity import nearby_active_events
from .models import Event

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Ontario511ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Track one geolocation entity per nearby active event."""
    coordinator = entry.runtime_data.events
    tracked: dict[str, EventLocation] = {}

    @callback
    def _sync() -> None:
        nearby = {event.id: km for event, km in nearby_active_events(hass, entry)}
        for event_id in tracked.keys() - nearby.keys():
            hass.async_create_task(
                tracked.pop(event_id).async_remove(force_remove=True)
            )
        new: list[EventLocation] = []
        for event_id, km in nearby.items():
            if (entity := tracked.get(event_id)) is None:
                entity = tracked[event_id] = EventLocation(coordinator, event_id)
                new.append(entity)
            entity.distance_km = km
        if new:
            async_add_entities(new)

    _sync()
    entry.async_on_unload(coordinator.async_add_listener(_sync))


class EventLocation(CoordinatorEntity[EventsCoordinator], GeolocationEvent):
    """A nearby traffic event on the map.

    These entities come and go with the events, so they have no unique ID and
    stay out of the entity registry.
    """

    _attr_attribution = ATTRIBUTION
    _attr_icon = "mdi:road-variant"
    _attr_source = DOMAIN
    _attr_unit_of_measurement = UnitOfLength.KILOMETERS

    def __init__(self, coordinator: EventsCoordinator, event_id: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.event_id = event_id
        self.distance_km: float | None = None
        self._attr_name = _event_name(coordinator.data[event_id])

    @property
    def _event(self) -> Event | None:
        return self.coordinator.data.get(self.event_id)

    @property
    def available(self) -> bool:
        """Return whether the event is still published."""
        return super().available and self._event is not None

    @property
    def distance(self) -> float | None:
        """Return the distance from home in kilometres."""
        return None if self.distance_km is None else round(self.distance_km, 1)

    @property
    def latitude(self) -> float | None:
        """Return the event's latitude."""
        event = self._event
        return event.coordinates[0] if event and event.coordinates else None

    @property
    def longitude(self) -> float | None:
        """Return the event's longitude."""
        event = self._event
        return event.coordinates[1] if event and event.coordinates else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return details of the event."""
        if (event := self._event) is None:
            return {}
        return {
            "external_id": event.id,
            "road": event.road,
            "direction": event.direction,
            "event_type": event.event_type,
            "event_subtype": event.event_subtype,
            "description": event.description,
            "lanes_affected": event.lanes_affected,
            "full_closure": event.is_full_closure,
            "start": event.start.isoformat() if event.start else None,
            "planned_end": event.planned_end.isoformat() if event.planned_end else None,
            "comment": event.comment,
        }


def _event_name(event: Event) -> str:
    parts = [event.road, event.direction, _EVENT_TYPE_NAMES.get(event.event_type or "")]
    return " ".join(part for part in parts if part) or f"Event {event.id}"


_EVENT_TYPE_NAMES = {
    "accidentsAndIncidents": "incident",
    "closures": "closure",
    "roadwork": "roadwork",
}
