"""Event entities that fire when new events or alerts appear.

They're stable entities whose state is the time of the last event, so
automations can notify on new closures without any temporary entities.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import Ontario511ConfigEntry, subentry_feeds, subentry_items
from .api import Resource
from .const import SUBENTRY_ITEM
from .coordinator import ResourceCoordinator
from .entity import ItemEntity, SummaryEntity
from .models import Alert, TrafficEvent

PARALLEL_UPDATES = 0

EVENT_NEW = "new_event"
EVENT_NEW_ALERT = "new_alert"


def _event_attributes(event: TrafficEvent) -> dict[str, Any]:
    return {
        "event_id": event.id,
        "road": event.road,
        "direction": event.direction,
        "type": event.event_type,
        "description": event.description,
        "full_closure": event.is_full_closure,
    }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Ontario511ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up event entities for segments, areas and commutes."""
    coordinators = entry.runtime_data.coordinators
    events = coordinators.get(Resource.EVENTS)
    road = coordinators.get(Resource.ROAD_CONDITIONS)
    alerts = coordinators.get(Resource.ALERTS)
    for subentry in entry.subentries.values():
        entities: list[EventEntity] = []
        if events is not None and road is not None:
            segments = subentry_items(subentry).get(Resource.ROAD_CONDITIONS, {})
            entities.extend(
                SegmentNewEvent(road, events, subentry.subentry_id, item_id, name)
                for item_id, name in segments.items()
            )
        if subentry.subentry_type != SUBENTRY_ITEM:
            feeds = subentry_feeds(subentry)
            if Resource.EVENTS in feeds and events is not None:
                entities.append(
                    ScopeNewEvent(events, road, subentry.subentry_id, subentry.title)
                )
            if Resource.ALERTS in feeds and alerts is not None:
                entities.append(
                    ScopeNewAlert(alerts, road, subentry.subentry_id, subentry.title)
                )
        async_add_entities(entities, config_subentry_id=subentry.subentry_id)


class _NewItemsMixin(EventEntity):
    """Fires once for each ID that wasn't there at the previous update.

    Must come first in the bases, ahead of the coordinator entity, so its
    update handler runs.

    The first update only records what's there, so nothing fires for events
    that already existed when Home Assistant started.
    """

    _seen: set[str] | None = None

    def _current(self) -> dict[str, dict[str, Any]]:
        raise NotImplementedError

    @callback
    def _handle_coordinator_update(self) -> None:
        current = self._current()
        if self._seen is not None:
            for item_id in current.keys() - self._seen:
                self._trigger_event(self._attr_event_types[0], current[item_id])
        self._seen = set(current)
        super()._handle_coordinator_update()  # type: ignore[misc]

    async def async_added_to_hass(self) -> None:
        """Record what's already there."""
        await super().async_added_to_hass()
        if self.available:
            self._seen = set(self._current())


class SegmentNewEvent(_NewItemsMixin, ItemEntity):
    """Fires when a new event appears on a road segment."""

    def __init__(
        self,
        road: ResourceCoordinator,
        events: ResourceCoordinator,
        subentry_id: str,
        item_id: str,
        item_name: str,
    ) -> None:
        """Initialize on the segment's device."""
        super().__init__(road, subentry_id, item_id, item_name, "segment_new_event")
        self.extra_coordinators = (events,)
        self._attr_event_types = [EVENT_NEW]

    def _current(self) -> dict[str, dict[str, Any]]:
        events, _ = self.coordinator.config_entry.runtime_data.feeds.on_segment(
            self.item_id
        )
        return {e.id: _event_attributes(e) for e in events}


class ScopeNewEvent(_NewItemsMixin, SummaryEntity):
    """Fires when a new event appears in an area or along a commute."""

    def __init__(
        self,
        events: ResourceCoordinator,
        road: ResourceCoordinator | None,
        subentry_id: str,
        subentry_title: str,
    ) -> None:
        """Initialize on the summary device."""
        super().__init__(events, subentry_id, subentry_title, "new_event")
        self.extra_coordinators = (road,) if road else ()
        self._attr_event_types = [EVENT_NEW]

    def _current(self) -> dict[str, dict[str, Any]]:
        scope = self.coordinator.config_entry.runtime_data.feeds.scope(self.subentry_id)
        return {e.id: _event_attributes(e) for e, _ in scope.events}


class ScopeNewAlert(_NewItemsMixin, SummaryEntity):
    """Fires when a new alert appears for an area's or commute's region."""

    def __init__(
        self,
        alerts: ResourceCoordinator,
        road: ResourceCoordinator | None,
        subentry_id: str,
        subentry_title: str,
    ) -> None:
        """Initialize on the summary device."""
        super().__init__(alerts, subentry_id, subentry_title, "new_alert")
        self.extra_coordinators = (road,) if road else ()
        self._attr_event_types = [EVENT_NEW_ALERT]

    def _current(self) -> dict[str, dict[str, Any]]:
        scope = self.coordinator.config_entry.runtime_data.feeds.scope(self.subentry_id)
        return {
            a.id: {
                "alert_id": a.id,
                "message": a.message,
                "regions": list(a.regions),
                "high_importance": a.high_importance,
            }
            for a in scope.alerts
            if isinstance(a, Alert)
        }
