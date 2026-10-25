"""Sensors for Ontario 511 items, road segments and area or commute summaries."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from . import Ontario511ConfigEntry, subentry_feeds, subentry_items
from .api import Resource
from .conditions import RoadCondition
from .const import CONF_EVENT_SLOTS, SUBENTRY_ITEM
from .coordinator import ResourceCoordinator
from .entity import ItemEntity, SummaryEntity, location_attributes
from .feeds import Scope
from .models import (
    CarpoolLot,
    FerryStatus,
    FerryTerminal,
    HovLane,
    InformationCentre,
    InspectionStation,
    RoadSegment,
    Roundabout,
    ServiceCentre,
    TrafficEvent,
    TransitHub,
)

PARALLEL_UPDATES = 0

# Long lists in attributes are capped and kept out of the recorder.
MAX_LISTED = 10
CONDITION_OPTIONS = [c.option for c in RoadCondition]
_SCOPE_KEYS = {
    Resource.EVENTS: "events",
    Resource.CONSTRUCTION: "construction",
    Resource.ALERTS: "alerts",
}


def _event_summary(event: TrafficEvent, km: float | None = None) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "road": event.road,
        "direction": event.direction,
        "type": event.event_type,
        "description": event.description,
        "full_closure": event.is_full_closure,
        "planned_end": event.planned_end.isoformat() if event.planned_end else None,
    }
    if km is not None:
        summary["distance_km"] = round(km, 1)
    return summary


@dataclass(frozen=True, kw_only=True)
class ItemSensorDescription(SensorEntityDescription):
    """Describes a sensor showing one field of an item."""

    resource: Resource
    value_fn: Callable[[Any], StateType | datetime]
    attrs_fn: Callable[[Any], dict[str, Any]] = lambda item: {}
    # The main entity carries the item's location for the map.
    main: bool = False


ITEM_SENSORS: tuple[ItemSensorDescription, ...] = (
    ItemSensorDescription(
        key="condition",
        resource=Resource.ROAD_CONDITIONS,
        device_class=SensorDeviceClass.ENUM,
        options=CONDITION_OPTIONS,
        value_fn=lambda s: s.condition.option,
        attrs_fn=lambda s: {
            "reported": list(s.conditions),
            "region": s.region,
            "last_updated": s.last_updated.isoformat() if s.last_updated else None,
        },
        main=True,
    ),
    ItemSensorDescription(
        key="visibility",
        resource=Resource.ROAD_CONDITIONS,
        value_fn=lambda s: s.visibility,
    ),
    ItemSensorDescription(
        key="ferry_status",
        resource=Resource.FERRY_TERMINALS,
        device_class=SensorDeviceClass.ENUM,
        options=list(FerryStatus.OPTIONS),
        value_fn=lambda f: f.status,
        attrs_fn=lambda f: {
            "details": f.details,
            "operated_by": f.operated_by,
            "website": f.website,
        },
        main=True,
    ),
    ItemSensorDescription(
        key="return_to_service",
        resource=Resource.FERRY_TERMINALS,
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda f: f.return_to_service,
    ),
    ItemSensorDescription(
        key="out_of_service",
        resource=Resource.FERRY_TERMINALS,
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda f: f.out_of_service,
        entity_registry_enabled_default=False,
    ),
    ItemSensorDescription(
        key="fuel",
        resource=Resource.SERVICE_CENTRES,
        value_fn=lambda c: c.fuel,
        attrs_fn=lambda c: {
            "road": c.road,
            "access_via": c.access_via,
            "food_services": c.food_services,
            "amenities": c.amenities,
            "commercial_parking": c.commercial_parking,
            "website": c.website,
        },
        main=True,
    ),
    ItemSensorDescription(
        key="parking_spaces",
        resource=Resource.CARPOOL_LOTS,
        value_fn=lambda c: c.parking_spaces,
        attrs_fn=lambda c: {
            "accessible_spaces": c.accessible_spaces,
            "interchange": c.interchange,
            "enter_from": c.enter_from,
            "bike_rack": c.bike_rack,
            "lights": c.lights,
            "transit": c.transit,
        },
        main=True,
    ),
    ItemSensorDescription(
        key="address",
        resource=Resource.TRANSIT_HUBS,
        value_fn=lambda h: h.address,
        attrs_fn=lambda h: {"website": h.website},
        main=True,
    ),
    ItemSensorDescription(
        key="station_location",
        resource=Resource.INSPECTION_STATIONS,
        value_fn=lambda s: s.location,
        attrs_fn=lambda s: {
            "highway": s.highway,
            "direction": s.direction,
            "contact": s.contact,
        },
        main=True,
    ),
    ItemSensorDescription(
        key="roundabout_type",
        resource=Resource.ROUNDABOUTS,
        value_fn=lambda r: r.roundabout_type,
        attrs_fn=lambda r: {"location": r.location},
        main=True,
    ),
    ItemSensorDescription(
        key="hov_road",
        resource=Resource.HOV_LANES,
        value_fn=lambda h: h.road,
        main=True,
    ),
    ItemSensorDescription(
        key="hours",
        resource=Resource.INFORMATION_CENTRES,
        value_fn=lambda c: c.hours,
        attrs_fn=lambda c: {
            "address": c.address,
            "phone": c.phone,
            "email": c.email,
        },
        main=True,
    ),
)

# Checked so a description can't be paired with the wrong model.
_MODELS: dict[Resource, type] = {
    Resource.ROAD_CONDITIONS: RoadSegment,
    Resource.FERRY_TERMINALS: FerryTerminal,
    Resource.SERVICE_CENTRES: ServiceCentre,
    Resource.CARPOOL_LOTS: CarpoolLot,
    Resource.TRANSIT_HUBS: TransitHub,
    Resource.INSPECTION_STATIONS: InspectionStation,
    Resource.ROUNDABOUTS: Roundabout,
    Resource.HOV_LANES: HovLane,
    Resource.INFORMATION_CENTRES: InformationCentre,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Ontario511ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Ontario 511 sensors."""
    coordinators = entry.runtime_data.coordinators
    road = coordinators.get(Resource.ROAD_CONDITIONS)
    for subentry in entry.subentries.values():
        entities: list[SensorEntity] = []
        for resource, items in subentry_items(subentry).items():
            if (coordinator := coordinators.get(resource)) is None:
                continue
            for item_id, name in items.items():
                entities.extend(
                    ItemSensor(coordinator, subentry.subentry_id, item_id, name, d)
                    for d in ITEM_SENSORS
                    if d.resource is resource
                )
                if resource is Resource.ROAD_CONDITIONS:
                    for feed in (Resource.EVENTS, Resource.CONSTRUCTION):
                        if feed_coordinator := coordinators.get(feed):
                            entities.append(
                                SegmentFeedSensor(
                                    coordinator,
                                    feed_coordinator,
                                    subentry.subentry_id,
                                    item_id,
                                    name,
                                )
                            )
        if subentry.subentry_type != SUBENTRY_ITEM:
            entities.extend(_summary_sensors(entry, subentry.subentry_id, road))
        async_add_entities(entities, config_subentry_id=subentry.subentry_id)


def _summary_sensors(
    entry: Ontario511ConfigEntry, subentry_id: str, road: ResourceCoordinator | None
) -> list[SensorEntity]:
    subentry = entry.subentries[subentry_id]
    coordinators = entry.runtime_data.coordinators
    feeds = subentry_feeds(subentry)
    title = subentry.title
    sensors: list[SensorEntity] = []
    if road is not None:
        sensors.append(WorstConditionSensor(road, subentry_id, title))
    for feed in (Resource.EVENTS, Resource.CONSTRUCTION, Resource.ALERTS):
        if feed in feeds and (coordinator := coordinators.get(feed)):
            sensors.append(ScopeCountSensor(coordinator, subentry_id, title, road))
    if Resource.EVENTS in feeds and (events := coordinators.get(Resource.EVENTS)):
        sensors.extend(
            NearestEventSensor(events, subentry_id, title, road, slot)
            for slot in range(1, int(subentry.data.get(CONF_EVENT_SLOTS, 0)) + 1)
        )
    return sensors


class ItemSensor(ItemEntity, SensorEntity):
    """One field of an item."""

    entity_description: ItemSensorDescription

    def __init__(
        self,
        coordinator: ResourceCoordinator,
        subentry_id: str,
        item_id: str,
        item_name: str,
        description: ItemSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, subentry_id, item_id, item_name, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Return whether the item is published and of the expected type."""
        return super().available and isinstance(
            self.item, _MODELS[self.entity_description.resource]
        )

    @property
    def native_value(self) -> StateType | datetime:
        """Return the field's value."""
        return self.entity_description.value_fn(self.item)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return details, plus the location on the main entity."""
        item = self.item
        attrs = self.entity_description.attrs_fn(item)
        if self.entity_description.main and item is not None:
            attrs |= location_attributes(item.coordinates)
        return attrs


class SegmentFeedSensor(ItemEntity, SensorEntity):
    """Active events or construction on a road segment."""

    _unrecorded_attributes = frozenset({"items"})

    def __init__(
        self,
        road: ResourceCoordinator,
        feed: ResourceCoordinator,
        subentry_id: str,
        item_id: str,
        item_name: str,
    ) -> None:
        """Initialize the sensor on the segment's device."""
        key = (
            "segment_events"
            if feed.resource is Resource.EVENTS
            else "segment_construction"
        )
        super().__init__(road, subentry_id, item_id, item_name, key)
        self.extra_coordinators = (feed,)
        self._feed = feed.resource

    def _items(self) -> list[TrafficEvent]:
        events, construction = (
            self.coordinator.config_entry.runtime_data.feeds.on_segment(self.item_id)
        )
        return events if self._feed is Resource.EVENTS else construction

    @property
    def native_value(self) -> int:
        """Return how many are active on the segment."""
        return len(self._items())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the first few, with their details."""
        return {"items": [_event_summary(e) for e in self._items()[:MAX_LISTED]]}


class WorstConditionSensor(SummaryEntity, SensorEntity):
    """The worst road condition in an area or along a commute."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = CONDITION_OPTIONS

    def __init__(
        self, road: ResourceCoordinator, subentry_id: str, subentry_title: str
    ) -> None:
        """Initialize the sensor."""
        super().__init__(road, subentry_id, subentry_title, "worst_condition")

    def _scope(self) -> Scope:
        return self.coordinator.config_entry.runtime_data.feeds.scope(self.subentry_id)

    @property
    def native_value(self) -> str:
        """Return the worst condition among the scope's road segments."""
        segments = self._scope().segments
        return max(
            (s.condition for s in segments), default=RoadCondition.UNKNOWN
        ).option

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the segments at that condition."""
        segments = self._scope().segments
        worst_condition = max(
            (s.condition for s in segments), default=RoadCondition.UNKNOWN
        )
        return {
            "segments": [s.name for s in segments if s.condition is worst_condition][
                :MAX_LISTED
            ],
            "segment_count": len(segments),
        }


class ScopeCountSensor(SummaryEntity, SensorEntity):
    """Active events, construction or alerts in an area or along a commute."""

    _unrecorded_attributes = frozenset({"items"})

    def __init__(
        self,
        feed: ResourceCoordinator,
        subentry_id: str,
        subentry_title: str,
        road: ResourceCoordinator | None,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(feed, subentry_id, subentry_title, _SCOPE_KEYS[feed.resource])
        self.extra_coordinators = (road,) if road else ()

    def _scope(self) -> Scope:
        return self.coordinator.config_entry.runtime_data.feeds.scope(self.subentry_id)

    @property
    def native_value(self) -> int:
        """Return how many are in scope."""
        scope = self._scope()
        if self.coordinator.resource is Resource.ALERTS:
            return len(scope.alerts)
        if self.coordinator.resource is Resource.EVENTS:
            return len(scope.events)
        return len(scope.construction)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the first few, nearest (or in route order) first."""
        scope = self._scope()
        if self.coordinator.resource is Resource.ALERTS:
            alerts = sorted(scope.alerts, key=lambda a: not a.high_importance)
            return {
                "items": [
                    {
                        "message": a.message,
                        "regions": list(a.regions),
                        "high_importance": a.high_importance,
                    }
                    for a in alerts[:MAX_LISTED]
                ]
            }
        pairs = (
            scope.events
            if self.coordinator.resource is Resource.EVENTS
            else scope.construction
        )
        return {"items": [_event_summary(e, km) for e, km in pairs[:MAX_LISTED]]}


class NearestEventSensor(SummaryEntity, SensorEntity):
    """The Nth nearest active event, with its location for the map.

    Slots are fixed entities; when there are fewer events than slots, the
    extra slots are unavailable.
    """

    def __init__(
        self,
        events: ResourceCoordinator,
        subentry_id: str,
        subentry_title: str,
        road: ResourceCoordinator | None,
        slot: int,
    ) -> None:
        """Initialize the slot."""
        super().__init__(
            events,
            subentry_id,
            subentry_title,
            "nearest_event",
            translation_placeholders={"slot": str(slot)},
        )
        self.extra_coordinators = (road,) if road else ()
        self._index = slot - 1

    def _event(self) -> tuple[TrafficEvent, float] | None:
        events = self.coordinator.config_entry.runtime_data.feeds.scope(
            self.subentry_id
        ).events
        return events[self._index] if self._index < len(events) else None

    @property
    def available(self) -> bool:
        """Return whether there's an event for this slot."""
        return super().available and self._event() is not None

    @property
    def native_value(self) -> str | None:
        """Return a short name for the event."""
        if (pair := self._event()) is None:
            return None
        event = pair[0]
        return (event.description or event.name)[:255]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the event's details and location."""
        if (pair := self._event()) is None:
            return {}
        event, km = pair
        return {
            "event_id": event.id,
            **_event_summary(event, km),
            "lanes_affected": event.lanes_affected,
            **location_attributes(event.coordinates),
        }
