"""Base entities for Ontario 511 items and area or commute summaries."""

from __future__ import annotations

from typing import Any

from homeassistant.const import ATTR_LATITUDE, ATTR_LONGITUDE
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import Resource
from .const import ATTRIBUTION, DOMAIN, SOURCE_URL
from .coordinator import ResourceCoordinator
from .geo import Point
from .models import Item

# Device model names, shown on each device's page.
RESOURCE_MODELS: dict[Resource, str] = {
    Resource.CAMERAS: "Traffic camera",
    Resource.CARPOOL_LOTS: "Carpool lot",
    Resource.FERRY_TERMINALS: "Ferry terminal",
    Resource.HOV_LANES: "HOT/HOV lane",
    Resource.INFORMATION_CENTRES: "Travel information centre",
    Resource.INSPECTION_STATIONS: "Inspection station",
    Resource.REST_AREAS: "Rest area",
    Resource.ROAD_CONDITIONS: "Road segment",
    Resource.ROUNDABOUTS: "Roundabout",
    Resource.SEASONAL_LOADS: "Seasonal load segment",
    Resource.SERVICE_CENTRES: "Service centre",
    Resource.TRANSIT_HUBS: "Transit hub",
    Resource.TRUCK_REST_AREAS: "Truck rest area",
}


# Items don't move, so their location is kept out of the recorder.
LOCATION_ATTRIBUTES = frozenset({ATTR_LATITUDE, ATTR_LONGITUDE})


def location_attributes(point: Point | None) -> dict[str, Any]:
    """Return map attributes for a location (the map card shows any entity
    with numeric latitude and longitude)."""
    if point is None:
        return {}
    return {ATTR_LATITUDE: point[0], ATTR_LONGITUDE: point[1]}


def item_device_identifier(subentry_id: str, resource: Resource, item_id: str) -> str:
    """Return the device identifier for an item within a subentry."""
    return f"{subentry_id}:{resource.value}:{item_id}"


def summary_device_identifier(subentry_id: str) -> str:
    """Return the device identifier for an area or commute summary."""
    return f"{subentry_id}:summary"


def _suggested_area(coordinator: ResourceCoordinator, subentry_id: str) -> str | None:
    """Return the Home Assistant area chosen for a subentry's devices.

    Home Assistant only applies it when a device is first created.
    """
    return coordinator.config_entry.runtime_data.suggested_areas.get(subentry_id)


class Ontario511Entity(CoordinatorEntity[ResourceCoordinator]):
    """An entity backed by an Ontario 511 coordinator.

    ``extra_coordinators`` also trigger state updates; feed entities depend on
    both the events and the road segments they're attached to.
    """

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True
    extra_coordinators: tuple[ResourceCoordinator, ...] = ()

    async def async_added_to_hass(self) -> None:
        """Listen to the extra coordinators too."""
        await super().async_added_to_hass()
        for coordinator in self.extra_coordinators:
            self.async_on_remove(
                coordinator.async_add_listener(self._handle_coordinator_update)
            )


class ItemEntity(Ontario511Entity):
    """An entity describing one item, on that item's device."""

    def __init__(
        self,
        coordinator: ResourceCoordinator,
        subentry_id: str,
        item_id: str,
        item_name: str,
        key: str,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.item_id = item_id
        self.subentry_id = subentry_id
        identifier = item_device_identifier(subentry_id, coordinator.resource, item_id)
        self._attr_translation_key = key
        self._attr_unique_id = f"{identifier}:{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identifier)},
            name=item_name,
            model=RESOURCE_MODELS[coordinator.resource],
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=SOURCE_URL,
            suggested_area=_suggested_area(coordinator, subentry_id),
        )

    @property
    def item(self) -> Item | None:
        """Return the item's current data, if it's still published."""
        return (self.coordinator.data or {}).get(self.item_id)

    @property
    def available(self) -> bool:
        """Return whether the item is still in the feed."""
        return super().available and self.item is not None


class SummaryEntity(Ontario511Entity):
    """An entity summarising an area or commute, on its summary device."""

    def __init__(
        self,
        coordinator: ResourceCoordinator,
        subentry_id: str,
        subentry_title: str,
        key: str,
        *,
        translation_placeholders: dict[str, str] | None = None,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.subentry_id = subentry_id
        identifier = summary_device_identifier(subentry_id)
        self._attr_translation_key = key
        self._attr_unique_id = f"{subentry_id}:{key}"
        if translation_placeholders:
            self._attr_translation_placeholders = translation_placeholders
            self._attr_unique_id += "_" + "_".join(translation_placeholders.values())
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identifier)},
            name=subentry_title,
            model="Summary",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=SOURCE_URL,
            suggested_area=_suggested_area(coordinator, subentry_id),
        )
