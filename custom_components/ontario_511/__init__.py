"""The Ontario 511 integration."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
import logging
from types import MappingProxyType
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigSubentry,
)
from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import slugify

from .api import Ontario511Client, Resource
from .card import async_register_card
from .client import create_client, key_id
from .commute import generation_lines, generation_segments
from .const import (
    CONF_CORRIDOR_KM,
    CONF_FEEDS,
    CONF_HA_AREA,
    CONF_ITEM_ID,
    CONF_ITEMS,
    CONF_LOCATION,
    CONF_PENDING,
    CONF_RADIUS_KM,
    CONF_RESOURCE,
    CONF_ROUTING,
    CONF_SHARE_ITEMS,
    DEFAULT_AREA_NAME,
    DEFAULT_RADIUS_KM,
    DOMAIN,
    MERGED_ITEMS_ISSUE,
    NEAREST_EVENTS,
    REPLAN_ISSUE,
    SUBENTRY_AREA,
    SUBENTRY_COMMUTE,
    SUBENTRY_ITEM,
    VALIDATION_RESOURCE,
)
from .coordinator import ResourceCoordinator
from .entity import SharedItem, item_device_identifier
from .feeds import FeedIndex
from .generation import (
    generate_for_area,
    generate_for_commute,
    inside_area,
    inside_corridor,
)
from .models import Item
from .websocket import async_register_commands

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

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
    # Home Assistant area name per subentry, suggested for its new devices.
    suggested_areas: dict[str, str] = field(default_factory=dict)


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


def shared_items(entry: ConfigEntry) -> dict[Resource, dict[str, SharedItem]]:
    """Return each real item once, with every subentry that lists it."""
    names: dict[Resource, dict[str, str]] = {}
    owners: dict[tuple[Resource, str], list[str]] = {}
    for subentry in entry.subentries.values():
        for resource, items in subentry_items(subentry).items():
            for item_id, name in items.items():
                names.setdefault(resource, {}).setdefault(item_id, name)
                owners.setdefault((resource, item_id), []).append(subentry.subentry_id)
    return {
        resource: {
            item_id: SharedItem(item_id, name, tuple(owners[(resource, item_id)]))
            for item_id, name in items.items()
        }
        for resource, items in names.items()
    }


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


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Serve the map card and the data it needs."""
    await async_register_card(hass)
    async_register_commands(hass)
    return True


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
    areas = ar.async_get(hass)
    for subentry in entry.subentries.values():
        if (area_id := subentry.data.get(CONF_HA_AREA)) and (
            area := areas.async_get_area(area_id)
        ):
            entry.runtime_data.suggested_areas[subentry.subentry_id] = area.name
    _async_update_replan_issues(hass, entry)
    _async_remove_unlisted_items(hass, entry)

    # Setup waits for one resource, which checks the key and connection; the
    # rest load in the background, because with many resources the rate
    # limiter spaces the first calls over more than a minute.
    missing = [c for c in coordinators.values() if c.data is None]
    if missing:
        first = min(missing, key=lambda c: c.resource is not VALIDATION_RESOURCE)
        await first.async_config_entry_first_refresh()
    first_refreshes = {
        coordinator.resource: entry.async_create_background_task(
            hass,
            coordinator.async_refresh(),
            f"{DOMAIN} first refresh {coordinator.resource.value}",
        )
        for coordinator in coordinators.values()
        if coordinator.data is None
    }
    if any(
        s.data.get(CONF_PENDING) or s.data.get(CONF_SHARE_ITEMS)
        for s in entry.subentries.values()
    ):
        entry.async_create_background_task(
            hass,
            _async_generate(hass, entry, first_refreshes),
            f"{DOMAIN} generate items",
        )

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove the entry's repair issues."""
    _async_update_replan_issues(hass, entry, removed=True)
    ir.async_delete_issue(hass, DOMAIN, f"{MERGED_ITEMS_ISSUE}_{entry.entry_id}")


def _async_remove_unlisted_items(
    hass: HomeAssistant, entry: Ontario511ConfigEntry
) -> None:
    """Remove item devices no area, commute or single item lists any more.

    Item devices belong to the entry, so Home Assistant doesn't remove them
    with a subentry.
    """
    wanted = {
        (DOMAIN, item_device_identifier(entry.entry_id, resource, item_id))
        for resource, items in shared_items(entry).items()
        for item_id in items
    }
    registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if device.config_subentry_id is None and not device.identifiers & wanted:
            registry.async_remove_device(device.id)


def _async_update_replan_issues(
    hass: HomeAssistant, entry: ConfigEntry, *, removed: bool = False
) -> None:
    """Ask for commutes planned before routes followed roads to be planned again.

    Planning sends the start and end to OpenStreetMap, so it's left to the
    user rather than done in the background.
    """
    wanted = {
        f"{REPLAN_ISSUE}_{s.subentry_id}": s
        for s in entry.subentries.values()
        if not removed
        and s.subentry_type == SUBENTRY_COMMUTE
        and CONF_ROUTING not in s.data
    }
    registry = ir.async_get(hass)
    for domain, issue_id in list(registry.issues):
        issue = registry.issues[(domain, issue_id)]
        if (
            domain == DOMAIN
            and issue_id.startswith(f"{REPLAN_ISSUE}_")
            and (issue.data or {}).get("entry_id") == entry.entry_id
            and issue_id not in wanted
        ):
            ir.async_delete_issue(hass, DOMAIN, issue_id)
    for issue_id, commute in wanted.items():
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=REPLAN_ISSUE,
            translation_placeholders={"commute": commute.title},
            data={"entry_id": entry.entry_id, "subentry_id": commute.subentry_id},
        )


async def async_unload_entry(hass: HomeAssistant, entry: Ontario511ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(
    hass: HomeAssistant, entry: Ontario511ConfigEntry
) -> None:
    """Reload when subentries change, so new items get entities."""
    if not entry.runtime_data.generating:
        hass.config_entries.async_schedule_reload(entry.entry_id)


async def _async_generate(
    hass: HomeAssistant,
    entry: Ontario511ConfigEntry,
    first_refreshes: Mapping[Resource, asyncio.Task[None]],
) -> None:
    """Generate items for subentries that asked for it at creation.

    Each area and commute lists everything of its types inside it, so an item
    stays while any of them still covers it. A resource whose data can't be
    fetched stays pending for the next setup.
    """
    runtime = entry.runtime_data
    pending = [
        s
        for s in entry.subentries.values()
        if s.data.get(CONF_PENDING) or s.data.get(CONF_SHARE_ITEMS)
    ]

    needed = {Resource(r) for s in pending for r in s.data.get(CONF_PENDING, [])}
    for subentry in pending:
        if subentry.data.get(CONF_SHARE_ITEMS):
            needed.update(r for r, i in subentry_items(subentry).items() if i)
    if any(s.subentry_type == SUBENTRY_COMMUTE for s in pending):
        needed.add(Resource.ROAD_CONDITIONS)
    # Waiting for the first refresh instead of starting another saves a call.
    for resource in sorted(needed):
        if (refresh := first_refreshes.get(resource)) is not None:
            await refresh

    listed = {
        resource: {shared.item_id: shared.name for shared in items.values()}
        for resource, items in shared_items(entry).items()
    }
    data = {
        resource: coordinator.data
        for resource, coordinator in runtime.coordinators.items()
        if coordinator.data is not None
    }
    # Commute corridors compare many points; keep that off the event loop.
    updates = await hass.async_add_executor_job(_plan_generation, pending, data, listed)

    changed = False
    runtime.generating = True
    try:
        for subentry_id, new_data in updates.items():
            changed |= hass.config_entries.async_update_subentry(
                entry, entry.subentries[subentry_id], data=new_data
            )
    finally:
        runtime.generating = False
    # Without a change, reloading would only retry a failing resource at once.
    if changed:
        hass.config_entries.async_schedule_reload(entry.entry_id)


def _plan_generation(
    pending: list[ConfigSubentry],
    data: dict[Resource, dict[str, Item]],
    listed: dict[Resource, dict[str, str]],
) -> dict[str, dict[str, Any]]:
    """Work out each pending subentry's new data."""
    updates: dict[str, dict[str, Any]] = {}
    for subentry in pending:
        chosen = subentry_items(subentry)
        still_pending: list[str] = []
        for value in subentry.data.get(CONF_PENDING, []):
            resource = Resource(value)
            if (items := data.get(resource)) is None:
                still_pending.append(value)
                continue
            exclude = set(chosen.get(resource, {}))
            if subentry.subentry_type == SUBENTRY_COMMUTE:
                generated = generate_for_commute(
                    resource,
                    items,
                    generation_segments(subentry.data),
                    generation_lines(subentry.data),
                    subentry.data[CONF_CORRIDOR_KM],
                    exclude,
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
            chosen.setdefault(resource, {}).update(generated.items)
        share_pending = bool(subentry.data.get(CONF_SHARE_ITEMS)) and (
            not _list_shared_items(subentry, chosen, data, listed)
        )
        new_data = {
            **subentry.data,
            CONF_ITEMS: {r.value: i for r, i in chosen.items() if i},
            CONF_PENDING: still_pending,
        }
        if not share_pending:
            new_data.pop(CONF_SHARE_ITEMS, None)
        updates[subentry.subentry_id] = new_data
    return updates


def _list_shared_items(
    subentry: ConfigSubentry,
    chosen: dict[Resource, dict[str, str]],
    data: dict[Resource, dict[str, Item]],
    listed: dict[Resource, dict[str, str]],
) -> bool:
    """Add items other subentries list that are inside this one.

    Only for types it follows, and only items that already exist, so nothing
    a user deleted comes back. Returns False if some data isn't available yet.
    """
    complete = True
    for resource, own in chosen.items():
        if not own:
            continue
        if (items := data.get(resource)) is None:
            complete = False
            continue
        others = [
            items[item_id]
            for item_id in listed.get(resource, {})
            if item_id not in own and item_id in items
        ]
        if subentry.subentry_type == SUBENTRY_COMMUTE:
            inside = inside_corridor(
                resource,
                others,
                generation_segments(subentry.data),
                generation_lines(subentry.data),
                subentry.data[CONF_CORRIDOR_KM],
            )
        else:
            location = subentry.data[CONF_LOCATION]
            inside = inside_area(
                resource,
                others,
                (location["latitude"], location["longitude"]),
                subentry.data[CONF_RADIUS_KM],
            )
        own.update({item_id: listed[resource][item_id] for item_id in inside})
    return complete


# Subentry data key for the configurable nearest event slots of 0.1.0-alpha.1.
_LEGACY_EVENT_SLOTS = "event_slots"


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate entries from earlier alpha builds.

    Version 1 had a radius option around home and only followed events and
    alerts. It becomes a "Home" area with the same radius and feeds.

    Version 2.1 let each area and commute choose how many nearest event slots
    to show. Version 2.2 always shows NEAREST_EVENTS, so extra slots go.

    Version 3 gives each real item one device, owned by the entry instead of
    the subentry that generated it.
    """
    if entry.version > 3:
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
                    }
                ),
                subentry_type=SUBENTRY_AREA,
                title=DEFAULT_AREA_NAME,
                unique_id=None,
            ),
        )
        hass.config_entries.async_update_entry(
            entry, options={}, version=2, minor_version=2
        )
    if entry.version == 2 and entry.minor_version < 2:
        registry = er.async_get(hass)
        for subentry in list(entry.subentries.values()):
            if _LEGACY_EVENT_SLOTS not in subentry.data:
                continue
            slots = int(subentry.data[_LEGACY_EVENT_SLOTS])
            for slot in range(NEAREST_EVENTS + 1, slots + 1):
                unique_id = f"{subentry.subentry_id}:nearest_event_{slot}"
                if entity_id := registry.async_get_entity_id(
                    Platform.SENSOR, DOMAIN, unique_id
                ):
                    registry.async_remove(entity_id)
            hass.config_entries.async_update_subentry(
                entry,
                subentry,
                data={
                    k: v for k, v in subentry.data.items() if k != _LEGACY_EVENT_SLOTS
                },
            )
        hass.config_entries.async_update_entry(entry, minor_version=2)
    if entry.version == 2:
        _async_share_item_devices(hass, entry)
        hass.config_entries.async_update_entry(entry, version=3, minor_version=1)
    return True


def _async_share_item_devices(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Move item devices from their subentries to the entry.

    Entity IDs, device IDs and user settings stay. If the same item had two
    devices, an enabled one stays over a disabled one, then a customised one,
    then the oldest; the other's entity IDs can't be merged into it, so a
    repair lists them.
    """
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    groups: dict[str, list[tuple[dr.DeviceEntry, str]]] = {}
    for device in dr.async_entries_for_config_entry(devices, entry.entry_id):
        for domain, identifier in device.identifiers:
            subentry_id, _, rest = identifier.partition(":")
            resource, _, item_id = rest.partition(":")
            if domain == DOMAIN and item_id and subentry_id in entry.subentries:
                new = f"{entry.entry_id}:{resource}:{item_id}"
                groups.setdefault(new, []).append((device, identifier))

    def own_entities(device: dr.DeviceEntry) -> list[er.RegistryEntry]:
        # A device can also carry other integrations' entities, such as helpers.
        return [
            e
            for e in er.async_entries_for_device(
                entities, device.id, include_disabled_entities=True
            )
            if e.config_entry_id == entry.entry_id
        ]

    lost: list[str] = []
    for new, group in groups.items():
        keep = min(
            group,
            key=lambda pair: (
                pair[0].disabled_by is not None,
                not _is_customised(pair[0], own_entities(pair[0])),
                pair[0].created_at,
            ),
        )
        for device, _ in group:
            if device.id != keep[0].id:
                lost.extend(e.entity_id for e in own_entities(device))
                devices.async_remove_device(device.id)
        device, old = keep
        # Entities first: moving the device first would delete them.
        for entity in own_entities(device):
            entities.async_update_entity(
                entity.entity_id,
                config_subentry_id=None,
                new_unique_id=new + entity.unique_id.removeprefix(old),
            )
        devices.async_update_device(
            device.id,
            new_config_subentry_id=None,
            new_identifiers={(DOMAIN, new)},
        )
    resources = {resource.value for resource in Resource}
    for entity in er.async_entries_for_config_entry(entities, entry.entry_id):
        subentry_id, _, rest = entity.unique_id.partition(":")
        if (
            entity.device_id is None
            and subentry_id in entry.subentries
            and rest.partition(":")[0] in resources
            and not entities.async_get_entity_id(
                entity.domain, DOMAIN, new_id := f"{entry.entry_id}:{rest}"
            )
        ):
            entities.async_update_entity(
                entity.entity_id, config_subentry_id=None, new_unique_id=new_id
            )
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_ITEM:
            hass.config_entries.async_update_subentry(
                entry, subentry, data={**subentry.data, CONF_SHARE_ITEMS: True}
            )
    if lost:
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"{MERGED_ITEMS_ISSUE}_{entry.entry_id}",
            is_fixable=False,
            is_persistent=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=MERGED_ITEMS_ISSUE,
            translation_placeholders={"entities": ", ".join(sorted(lost))},
        )


def _is_customised(device: dr.DeviceEntry, entities: list[er.RegistryEntry]) -> bool:
    """Return whether the user changed a device or one of its entities."""
    if device.name_by_user or device.area_id or device.labels:
        return True
    for entity in entities:
        if entity.name or entity.icon or entity.area_id or entity.labels:
            return True
        default = slugify(f"{device.name or ''} {entity.original_name or ''}")
        if not entity.entity_id.partition(".")[2].startswith(default):
            return True
    return False


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: Ontario511ConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Let users delete an item's device, from everywhere that lists it."""
    if device.config_subentry_id is not None:
        # A summary device goes away with its area or commute.
        return False
    prefix = f"{entry.entry_id}:"
    for domain, identifier in device.identifiers:
        if domain != DOMAIN or not identifier.startswith(prefix):
            continue
        resource_value, _, item_id = identifier[len(prefix) :].partition(":")
        loaded = entry.state is ConfigEntryState.LOADED
        # One reload for the whole change, not one per subentry.
        if loaded:
            entry.runtime_data.generating = True
        try:
            _async_drop_item(hass, entry, resource_value, item_id)
        finally:
            if loaded:
                entry.runtime_data.generating = False
        if loaded:
            hass.config_entries.async_schedule_reload(entry.entry_id)
    return True


def _async_drop_item(
    hass: HomeAssistant, entry: ConfigEntry, resource_value: str, item_id: str
) -> None:
    """Remove an item from every subentry that lists it."""
    for subentry in list(entry.subentries.values()):
        if subentry.subentry_type == SUBENTRY_ITEM:
            if (
                subentry.data[CONF_RESOURCE] == resource_value
                and subentry.data[CONF_ITEM_ID] == item_id
            ):
                hass.config_entries.async_remove_subentry(entry, subentry.subentry_id)
            continue
        stored = subentry.data.get(CONF_ITEMS, {})
        if item_id not in stored.get(resource_value, {}):
            continue
        items = {
            r: {
                i: n
                for i, n in names.items()
                if not (r == resource_value and i == item_id)
            }
            for r, names in stored.items()
        }
        hass.config_entries.async_update_subentry(
            entry, subentry, data={**subentry.data, CONF_ITEMS: items}
        )
