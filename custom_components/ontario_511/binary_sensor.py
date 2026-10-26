"""Binary sensors for Ontario 511 items, road segments and summaries."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import Ontario511ConfigEntry, subentry_feeds, subentry_items
from .api import Resource
from .const import SUBENTRY_ITEM
from .coordinator import ResourceCoordinator
from .entity import ItemEntity, SummaryEntity
from .models import CarpoolLot, RestArea, RoadSegment, SeasonalLoad

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class ItemBinaryDescription(BinarySensorEntityDescription):
    """Describes a binary sensor derived from an item."""

    resource: Resource
    model: type
    value_fn: Callable[[Any], bool | None]


def _flag(
    key: str, resource: Resource, model: type, field: str
) -> ItemBinaryDescription:
    """Describe a binary sensor showing one yes/no field of an item."""
    return ItemBinaryDescription(
        key=key,
        resource=resource,
        model=model,
        value_fn=lambda item: getattr(item, field),
    )


def _rest_area_binary_sensors(
    resource: Resource,
) -> tuple[ItemBinaryDescription, ...]:
    return (
        # The Status sensor carries the same; this is for simple on/off use.
        ItemBinaryDescription(
            key="open",
            resource=resource,
            model=RestArea,
            device_class=BinarySensorDeviceClass.OPENING,
            value_fn=lambda a: a.is_open,
            entity_registry_enabled_default=False,
        ),
        _flag("truck_parking", resource, RestArea, "truck_parking"),
        _flag("washrooms", resource, RestArea, "lavatory"),
        _flag("accessible", resource, RestArea, "accessible"),
        _flag("fuel", resource, RestArea, "fuel"),
    )


ITEM_BINARY_SENSORS: tuple[ItemBinaryDescription, ...] = (
    # Also part of Road condition ("Drifting snow"), so off by default.
    ItemBinaryDescription(
        key="drifting",
        resource=Resource.ROAD_CONDITIONS,
        model=RoadSegment,
        value_fn=lambda s: s.drifting,
        entity_registry_enabled_default=False,
    ),
    ItemBinaryDescription(
        key="hazardous",
        resource=Resource.ROAD_CONDITIONS,
        model=RoadSegment,
        device_class=BinarySensorDeviceClass.SAFETY,
        value_fn=lambda s: s.is_hazardous,
    ),
    *_rest_area_binary_sensors(Resource.REST_AREAS),
    *_rest_area_binary_sensors(Resource.TRUCK_REST_AREAS),
    ItemBinaryDescription(
        key="restricted",
        resource=Resource.SEASONAL_LOADS,
        model=SeasonalLoad,
        value_fn=lambda s: s.restricted,
        entity_registry_enabled_default=False,
    ),
    _flag("bike_rack", Resource.CARPOOL_LOTS, CarpoolLot, "bike_rack"),
    _flag("lights", Resource.CARPOOL_LOTS, CarpoolLot, "lights"),
    _flag("transit", Resource.CARPOOL_LOTS, CarpoolLot, "transit"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Ontario511ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Ontario 511 binary sensors."""
    coordinators = entry.runtime_data.coordinators
    road = coordinators.get(Resource.ROAD_CONDITIONS)
    feeds = [
        c
        for r in (Resource.EVENTS, Resource.CONSTRUCTION)
        if (c := coordinators.get(r)) is not None
    ]
    for subentry in entry.subentries.values():
        entities: list[BinarySensorEntity] = []
        for resource, items in subentry_items(subentry).items():
            if (coordinator := coordinators.get(resource)) is None:
                continue
            for item_id, name in items.items():
                entities.extend(
                    ItemBinarySensor(
                        coordinator, subentry.subentry_id, item_id, name, d
                    )
                    for d in ITEM_BINARY_SENSORS
                    if d.resource is resource
                )
                if resource is Resource.ROAD_CONDITIONS and feeds:
                    entities.append(
                        SegmentClosureSensor(
                            coordinator, feeds, subentry.subentry_id, item_id, name
                        )
                    )
        if subentry.subentry_type != SUBENTRY_ITEM:
            followed = [c for c in feeds if c.resource in subentry_feeds(subentry)]
            if road is not None:
                entities.append(
                    HazardousConditionsSensor(
                        road, subentry.subentry_id, subentry.title
                    )
                )
            if followed:
                entities.append(
                    ScopeClosureSensor(
                        followed, road, subentry.subentry_id, subentry.title
                    )
                )
        async_add_entities(entities, config_subentry_id=subentry.subentry_id)


class ItemBinarySensor(ItemEntity, BinarySensorEntity):
    """A yes/no fact about an item."""

    entity_description: ItemBinaryDescription

    def __init__(
        self,
        coordinator: ResourceCoordinator,
        subentry_id: str,
        item_id: str,
        item_name: str,
        description: ItemBinaryDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, subentry_id, item_id, item_name, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Return whether the item is published and of the expected type."""
        return super().available and isinstance(
            self.item, self.entity_description.model
        )

    @property
    def is_on(self) -> bool | None:
        """Return the fact's value."""
        return self.entity_description.value_fn(self.item)


class SegmentClosureSensor(ItemEntity, BinarySensorEntity):
    """On while an active event or project closes all lanes of a segment."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(
        self,
        road: ResourceCoordinator,
        feeds: list[ResourceCoordinator],
        subentry_id: str,
        item_id: str,
        item_name: str,
    ) -> None:
        """Initialize the binary sensor on the segment's device."""
        super().__init__(road, subentry_id, item_id, item_name, "full_closure")
        self.extra_coordinators = tuple(feeds)

    @property
    def is_on(self) -> bool:
        """Return whether anything on the segment closes all lanes."""
        events, construction = (
            self.coordinator.config_entry.runtime_data.feeds.on_segment(
                self.item_id, self.subentry_id
            )
        )
        return any(e.is_full_closure for e in (*events, *construction))


class HazardousConditionsSensor(SummaryEntity, BinarySensorEntity):
    """On while any road segment in scope is hazardous.

    Hazardous means worse than wet (partly covered or worse), or drifting
    snow, or poor visibility.
    """

    _attr_device_class = BinarySensorDeviceClass.SAFETY

    def __init__(
        self, road: ResourceCoordinator, subentry_id: str, subentry_title: str
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(road, subentry_id, subentry_title, "hazardous_conditions")

    @property
    def is_on(self) -> bool:
        """Return whether any segment is hazardous."""
        scope = self.coordinator.config_entry.runtime_data.feeds.scope(self.subentry_id)
        return any(s.is_hazardous for s in scope.segments)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the hazardous segments."""
        scope = self.coordinator.config_entry.runtime_data.feeds.scope(self.subentry_id)
        return {"segments": [s.name for s in scope.segments if s.is_hazardous]}


class ScopeClosureSensor(SummaryEntity, BinarySensorEntity):
    """On while an active event or project in scope closes all lanes."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(
        self,
        feeds: list[ResourceCoordinator],
        road: ResourceCoordinator | None,
        subentry_id: str,
        subentry_title: str,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(feeds[0], subentry_id, subentry_title, "closure")
        self.extra_coordinators = tuple(feeds[1:]) + ((road,) if road else ())
        self._feeds = {c.resource for c in feeds}

    @property
    def is_on(self) -> bool:
        """Return whether anything in scope closes all lanes."""
        scope = self.coordinator.config_entry.runtime_data.feeds.scope(self.subentry_id)
        pairs = [
            *(scope.events if Resource.EVENTS in self._feeds else []),
            *(scope.construction if Resource.CONSTRUCTION in self._feeds else []),
        ]
        return any(event.is_full_closure for event, _ in pairs)
