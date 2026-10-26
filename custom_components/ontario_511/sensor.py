"""Sensors for Ontario 511 items, road segments and area or commute summaries."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import MAX_LENGTH_STATE_STATE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from . import Ontario511ConfigEntry, subentry_feeds, subentry_items
from .api import Resource
from .conditions import RoadCondition
from .const import NEAREST_EVENTS, SUBENTRY_ITEM
from .coordinator import ResourceCoordinator
from .entity import (
    LOCATION_ATTRIBUTES,
    ItemEntity,
    SummaryEntity,
    location_attributes,
)
from .feeds import Scope
from .models import (
    Camera as CameraModel,
    CarpoolLot,
    FerryStatus,
    FerryTerminal,
    HovLane,
    InformationCentre,
    InspectionStation,
    RestArea,
    RoadSegment,
    Roundabout,
    SeasonalLoad,
    ServiceCentre,
    TrafficEvent,
    TransitHub,
)

PARALLEL_UPDATES = 0

# Long lists in attributes are capped and kept out of the recorder.
MAX_LISTED = 10
CONDITION_OPTIONS = RoadCondition.options()
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
    model: type
    value_fn: Callable[[Any], StateType | date | datetime]
    # Only for values that explain the state, such as the feed's own text.
    attrs_fn: Callable[[Any], dict[str, Any]] = lambda item: {}
    # The main entity carries the item's location for the map.
    main: bool = False


def _short(value: str | None) -> str | None:
    """Return text that fits in a state."""
    return value[:MAX_LENGTH_STATE_STATE] if value else None


def _text(
    key: str,
    resource: Resource,
    model: type,
    field: str,
    *,
    enabled: bool = True,
    category: EntityCategory | None = None,
) -> ItemSensorDescription:
    """Describe a sensor showing one text field of an item."""
    return ItemSensorDescription(
        key=key,
        resource=resource,
        model=model,
        value_fn=lambda item: _short(getattr(item, field)),
        entity_registry_enabled_default=enabled,
        entity_category=category,
    )


def _rest_area_sensors(resource: Resource) -> tuple[ItemSensorDescription, ...]:
    return (
        _text("area_type", resource, RestArea, "area_type"),
        _text("road", resource, RestArea, "road"),
        _text("direction", resource, RestArea, "direction"),
        _text("location", resource, RestArea, "location"),
        _text("season", resource, RestArea, "season"),
        _text("food_services", resource, RestArea, "food_services"),
        _text("comments", resource, RestArea, "comments"),
    )


ITEM_SENSORS: tuple[ItemSensorDescription, ...] = (
    ItemSensorDescription(
        key="condition",
        resource=Resource.ROAD_CONDITIONS,
        model=RoadSegment,
        device_class=SensorDeviceClass.ENUM,
        options=CONDITION_OPTIONS,
        value_fn=lambda s: s.condition.option,
        attrs_fn=lambda s: {"reported": list(s.conditions)},
        main=True,
    ),
    _text("visibility", Resource.ROAD_CONDITIONS, RoadSegment, "visibility"),
    _text("road", Resource.ROAD_CONDITIONS, RoadSegment, "road"),
    ItemSensorDescription(
        key="last_updated",
        resource=Resource.ROAD_CONDITIONS,
        model=RoadSegment,
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.last_updated,
    ),
    _text("road", Resource.CAMERAS, CameraModel, "road"),
    _text("direction", Resource.CAMERAS, CameraModel, "direction"),
    _text(
        "source",
        Resource.CAMERAS,
        CameraModel,
        "source",
        enabled=False,
        category=EntityCategory.DIAGNOSTIC,
    ),
    ItemSensorDescription(
        key="ferry_status",
        resource=Resource.FERRY_TERMINALS,
        model=FerryTerminal,
        device_class=SensorDeviceClass.ENUM,
        options=list(FerryStatus.OPTIONS),
        value_fn=lambda f: f.status,
        attrs_fn=lambda f: {"reported": f.status_text},
        main=True,
    ),
    _text("details", Resource.FERRY_TERMINALS, FerryTerminal, "details"),
    ItemSensorDescription(
        key="return_to_service",
        resource=Resource.FERRY_TERMINALS,
        model=FerryTerminal,
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda f: f.return_to_service,
    ),
    ItemSensorDescription(
        key="out_of_service",
        resource=Resource.FERRY_TERMINALS,
        model=FerryTerminal,
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda f: f.out_of_service,
        entity_registry_enabled_default=False,
    ),
    _text("operated_by", Resource.FERRY_TERMINALS, FerryTerminal, "operated_by"),
    _text("website", Resource.FERRY_TERMINALS, FerryTerminal, "website", enabled=False),
    *_rest_area_sensors(Resource.REST_AREAS),
    *_rest_area_sensors(Resource.TRUCK_REST_AREAS),
    ItemSensorDescription(
        key="fuel",
        resource=Resource.SERVICE_CENTRES,
        model=ServiceCentre,
        value_fn=lambda c: _short(c.fuel),
        main=True,
    ),
    _text("road", Resource.SERVICE_CENTRES, ServiceCentre, "road"),
    _text("access_via", Resource.SERVICE_CENTRES, ServiceCentre, "access_via"),
    _text("food_services", Resource.SERVICE_CENTRES, ServiceCentre, "food_services"),
    _text("amenities", Resource.SERVICE_CENTRES, ServiceCentre, "amenities"),
    ItemSensorDescription(
        key="commercial_parking",
        resource=Resource.SERVICE_CENTRES,
        model=ServiceCentre,
        value_fn=lambda c: c.commercial_parking,
    ),
    _text("website", Resource.SERVICE_CENTRES, ServiceCentre, "website", enabled=False),
    _text("route", Resource.SEASONAL_LOADS, SeasonalLoad, "route"),
    _text("load_status", Resource.SEASONAL_LOADS, SeasonalLoad, "status"),
    ItemSensorDescription(
        key="load_date",
        resource=Resource.SEASONAL_LOADS,
        model=SeasonalLoad,
        device_class=SensorDeviceClass.DATE,
        value_fn=lambda s: s.date,
    ),
    ItemSensorDescription(
        key="parking_spaces",
        resource=Resource.CARPOOL_LOTS,
        model=CarpoolLot,
        value_fn=lambda c: c.parking_spaces,
        main=True,
    ),
    ItemSensorDescription(
        key="accessible_spaces",
        resource=Resource.CARPOOL_LOTS,
        model=CarpoolLot,
        value_fn=lambda c: c.accessible_spaces,
    ),
    _text("road", Resource.CARPOOL_LOTS, CarpoolLot, "highway"),
    _text("interchange", Resource.CARPOOL_LOTS, CarpoolLot, "interchange"),
    _text("enter_from", Resource.CARPOOL_LOTS, CarpoolLot, "enter_from"),
    ItemSensorDescription(
        key="address",
        resource=Resource.TRANSIT_HUBS,
        model=TransitHub,
        value_fn=lambda h: _short(h.address),
        main=True,
    ),
    _text("website", Resource.TRANSIT_HUBS, TransitHub, "website", enabled=False),
    ItemSensorDescription(
        key="station_location",
        resource=Resource.INSPECTION_STATIONS,
        model=InspectionStation,
        value_fn=lambda s: _short(s.location),
        main=True,
    ),
    _text("road", Resource.INSPECTION_STATIONS, InspectionStation, "highway"),
    _text("direction", Resource.INSPECTION_STATIONS, InspectionStation, "direction"),
    _text("contact", Resource.INSPECTION_STATIONS, InspectionStation, "contact"),
    ItemSensorDescription(
        key="roundabout_type",
        resource=Resource.ROUNDABOUTS,
        model=Roundabout,
        value_fn=lambda r: _short(r.roundabout_type),
        main=True,
    ),
    _text("location", Resource.ROUNDABOUTS, Roundabout, "location"),
    ItemSensorDescription(
        key="hov_road",
        resource=Resource.HOV_LANES,
        model=HovLane,
        value_fn=lambda h: _short(h.road),
        main=True,
    ),
    ItemSensorDescription(
        key="hours",
        resource=Resource.INFORMATION_CENTRES,
        model=InformationCentre,
        value_fn=lambda c: _short(c.hours),
        main=True,
    ),
    _text("centre_address", Resource.INFORMATION_CENTRES, InformationCentre, "address"),
    _text("phone", Resource.INFORMATION_CENTRES, InformationCentre, "phone"),
    _text("email", Resource.INFORMATION_CENTRES, InformationCentre, "email"),
)


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
            for slot in range(1, NEAREST_EVENTS + 1)
        )
    return sensors


class ItemSensor(ItemEntity, SensorEntity):
    """One field of an item."""

    _unrecorded_attributes = LOCATION_ATTRIBUTES

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
            self.item, self.entity_description.model
        )

    @property
    def native_value(self) -> StateType | date | datetime:
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
            self.coordinator.config_entry.runtime_data.feeds.on_segment(
                self.item_id, self.subentry_id
            )
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
    def native_value(self) -> str | None:
        """Return the worst condition among the scope's road segments.

        None (Home Assistant's Unknown) when no segment has a report.
        """
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

    There are always NEAREST_EVENTS of these; with fewer events in scope, the
    rest are Unknown.
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
