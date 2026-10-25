"""The Ontario 511 integration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
import logging
from types import MappingProxyType
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .api import Ontario511Client, Resource
from .client import create_client, key_id
from .const import (
    CONF_CORRIDOR_KM,
    CONF_EVENT_SLOTS,
    CONF_FEEDS,
    CONF_ITEM_ID,
    CONF_ITEMS,
    CONF_LOCATION,
    CONF_PENDING,
    CONF_RADIUS_KM,
    CONF_RESOURCE,
    CONF_SEGMENTS,
    DEFAULT_AREA_NAME,
    DEFAULT_EVENT_SLOTS,
    DEFAULT_RADIUS_KM,
    DOMAIN,
    SUBENTRY_AREA,
    SUBENTRY_COMMUTE,
    SUBENTRY_ITEM,
    VALIDATION_RESOURCE,
)
from .coordinator import ResourceCoordinator
from .feeds import FeedIndex
from .generation import generate_for_area, generate_for_commute
from .models import Item, RoadSegment

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CAMERA,
    Platform.EVENT,
    Platform.SENSOR,
]


@dataclass
class Ontario511Data:
    """Runtime data for a config entry."""

    client: Ontario511Client
    key: str
    coordinators: dict[Resource, ResourceCoordinator]
    feeds: FeedIndex = field(init=False)
    # Camera images by URL, with when they were fetched.
    camera_images: dict[str, tuple[datetime, bytes]] = field(default_factory=dict)
    # Set while generation updates subentries, so those updates don't each
    # trigger a reload.
    generating: bool = field(default=False)


type Ontario511ConfigEntry = ConfigEntry[Ontario511Data]


def subentry_items(subentry: ConfigSubentry) -> dict[Resource, dict[str, str]]:
    """Return a subentry's items as {resource: {item ID: name}}."""
    if subentry.subentry_type == SUBENTRY_ITEM:
        return {
            Resource(subentry.data[CONF_RESOURCE]): {
                subentry.data[CONF_ITEM_ID]: subentry.title
            }
        }
    stored: Mapping[str, Mapping[str, str]] = subentry.data.get(CONF_ITEMS, {})
    return {Resource(r): dict(items) for r, items in stored.items()}


def subentry_feeds(subentry: ConfigSubentry) -> set[Resource]:
    """Return the feeds (events, construction, alerts) a subentry follows."""
    return {Resource(r) for r in subentry.data.get(CONF_FEEDS, [])}


def resources_in_use(entry: ConfigEntry) -> set[Resource]:
    """Return every resource the entry's subentries need data for."""
    needed: set[Resource] = set()
    for subentry in entry.subentries.values():
        needed.update(r for r, items in subentry_items(subentry).items() if items)
        needed.update(subentry_feeds(subentry))
        needed.update(Resource(r) for r in subentry.data.get(CONF_PENDING, []))
        if subentry.subentry_type == SUBENTRY_COMMUTE:
            needed.add(Resource.ROAD_CONDITIONS)
    # Events and construction are attached to road segments.
    if needed & {Resource.EVENTS, Resource.CONSTRUCTION}:
        needed.add(Resource.ROAD_CONDITIONS)
    return needed


async def async_setup_entry(hass: HomeAssistant, entry: Ontario511ConfigEntry) -> bool:
    """Set up Ontario 511 from a config entry."""
    client = create_client(hass, entry.data[CONF_API_KEY])
    key = key_id(entry.data[CONF_API_KEY])
    coordinators = {
        resource: ResourceCoordinator(hass, entry, client, resource, key)
        for resource in sorted(resources_in_use(entry))
    }
    entry.runtime_data = Ontario511Data(client, key, coordinators)
    entry.runtime_data.feeds = FeedIndex(entry)

    # Setup waits for one resource, which checks the key and connection; the
    # rest load in the background, because with many resources the rate
    # limiter spaces the first calls over more than a minute.
    missing = [c for c in coordinators.values() if c.data is None]
    if missing:
        first = min(missing, key=lambda c: c.resource is not VALIDATION_RESOURCE)
        await first.async_config_entry_first_refresh()
    for coordinator in coordinators.values():
        if coordinator.data is None:
            entry.async_create_background_task(
                hass,
                coordinator.async_refresh(),
                f"{DOMAIN} first refresh {coordinator.resource.value}",
            )
    if any(s.data.get(CONF_PENDING) for s in entry.subentries.values()):
        entry.async_create_background_task(
            hass, _async_generate(hass, entry), f"{DOMAIN} generate items"
        )

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: Ontario511ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(
    hass: HomeAssistant, entry: Ontario511ConfigEntry
) -> None:
    """Reload when subentries change, so new items get entities."""
    if not entry.runtime_data.generating:
        hass.config_entries.async_schedule_reload(entry.entry_id)


async def _async_generate(hass: HomeAssistant, entry: Ontario511ConfigEntry) -> None:
    """Generate items for subentries that asked for it at creation.

    Commutes go first, so their items take priority; areas skip items another
    subentry already has. A resource whose data can't be fetched stays pending
    for the next setup.
    """
    runtime = entry.runtime_data
    pending = [s for s in entry.subentries.values() if s.data.get(CONF_PENDING)]
    pending.sort(key=lambda s: s.subentry_type != SUBENTRY_COMMUTE)

    needed = {Resource(r) for s in pending for r in s.data[CONF_PENDING]}
    if any(s.subentry_type == SUBENTRY_COMMUTE for s in pending):
        needed.add(Resource.ROAD_CONDITIONS)
    for resource in sorted(needed):
        coordinator = runtime.coordinators[resource]
        if coordinator.data is None:
            await coordinator.async_refresh()

    claimed: dict[Resource, set[str]] = {}
    for subentry in entry.subentries.values():
        for resource, items in subentry_items(subentry).items():
            claimed.setdefault(resource, set()).update(items)
    data = {
        resource: coordinator.data
        for resource, coordinator in runtime.coordinators.items()
        if coordinator.data is not None
    }
    # Commute corridors compare many points; keep that off the event loop.
    updates = await hass.async_add_executor_job(
        _plan_generation, pending, data, claimed
    )

    runtime.generating = True
    try:
        for subentry_id, new_data in updates.items():
            hass.config_entries.async_update_subentry(
                entry, entry.subentries[subentry_id], data=new_data
            )
    finally:
        runtime.generating = False
    hass.config_entries.async_schedule_reload(entry.entry_id)


def _plan_generation(
    pending: list[ConfigSubentry],
    data: dict[Resource, dict[str, Item]],
    claimed: dict[Resource, set[str]],
) -> dict[str, dict[str, Any]]:
    """Work out each pending subentry's new data, in priority order."""
    updates: dict[str, dict[str, Any]] = {}
    for subentry in pending:
        chosen = subentry_items(subentry)
        still_pending: list[str] = []
        for value in subentry.data[CONF_PENDING]:
            resource = Resource(value)
            if (items := data.get(resource)) is None:
                still_pending.append(value)
                continue
            exclude = claimed.setdefault(resource, set())
            if subentry.subentry_type == SUBENTRY_COMMUTE:
                segments = data.get(Resource.ROAD_CONDITIONS, {})
                route = [
                    s
                    for i in subentry.data[CONF_SEGMENTS]
                    if isinstance(s := segments.get(i), RoadSegment)
                ]
                generated = generate_for_commute(
                    resource, items, route, subentry.data[CONF_CORRIDOR_KM], exclude
                )
            else:
                location = subentry.data[CONF_LOCATION]
                generated = generate_for_area(
                    resource,
                    items,
                    (location["latitude"], location["longitude"]),
                    subentry.data[CONF_RADIUS_KM],
                    exclude,
                )
            exclude.update(generated.items)
            chosen.setdefault(resource, {}).update(generated.items)
        updates[subentry.subentry_id] = {
            **subentry.data,
            CONF_ITEMS: {r.value: i for r, i in chosen.items() if i},
            CONF_PENDING: still_pending,
        }
    return updates


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate entries from the first alpha builds.

    Version 1 had a radius option around home and only followed events and
    alerts. It becomes a "Home" area with the same radius and feeds.
    """
    if entry.version > 2:
        return False
    if entry.version == 1:
        hass.config_entries.async_add_subentry(
            entry,
            ConfigSubentry(
                data=MappingProxyType(
                    {
                        CONF_LOCATION: {
                            "latitude": hass.config.latitude,
                            "longitude": hass.config.longitude,
                        },
                        CONF_RADIUS_KM: float(
                            entry.options.get("radius", DEFAULT_RADIUS_KM)
                        ),
                        CONF_FEEDS: [Resource.EVENTS.value, Resource.ALERTS.value],
                        CONF_PENDING: [],
                        CONF_ITEMS: {},
                        CONF_EVENT_SLOTS: DEFAULT_EVENT_SLOTS,
                    }
                ),
                subentry_type=SUBENTRY_AREA,
                title=DEFAULT_AREA_NAME,
                unique_id=None,
            ),
        )
        hass.config_entries.async_update_entry(entry, options={}, version=2)
    return True


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: Ontario511ConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Let users delete an item's device; the item is dropped from its subentry."""
    for domain, identifier in device.identifiers:
        if domain != DOMAIN:
            continue
        subentry_id, _, rest = identifier.partition(":")
        if (subentry := entry.subentries.get(subentry_id)) is None:
            continue
        resource_value, _, item_id = rest.partition(":")
        if not item_id:
            # A summary device goes away with its area or commute.
            return False
        if subentry.subentry_type == SUBENTRY_ITEM:
            hass.config_entries.async_remove_subentry(entry, subentry_id)
            return True
        items = {
            r: {
                i: n
                for i, n in names.items()
                if not (r == resource_value and i == item_id)
            }
            for r, names in subentry.data.get(CONF_ITEMS, {}).items()
        }
        hass.config_entries.async_update_subentry(
            entry, subentry, data={**subentry.data, CONF_ITEMS: items}
        )
        return True
    return True
