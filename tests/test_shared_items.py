"""Tests for items shared between areas and commutes, and the move to them."""

from __future__ import annotations

import hashlib
from unittest.mock import Mock, patch

from homeassistant.config_entries import ConfigEntryState, ConfigSubentryDataWithId
from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    load_json_array_fixture,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.ontario_511 import async_remove_config_entry_device
from custom_components.ontario_511.api import Resource
from custom_components.ontario_511.const import (
    CONF_ITEM_ID,
    CONF_ITEMS,
    CONF_PENDING,
    CONF_RESOURCE,
    CONF_SHARE_ITEMS,
    DOMAIN,
    MERGED_ITEMS_ISSUE,
    SUBENTRY_AREA,
    SUBENTRY_ITEM,
)
from custom_components.ontario_511.coordinator import parse_items
from custom_components.ontario_511.feeds import FeedIndex, Scope
from custom_components.ontario_511.models import TrafficEvent

from .conftest import API_KEY, ENTRY_ID, area_data

# The two documented camera samples, on Highway 407 near Bowmanville.
SPOT = {"latitude": 43.995, "longitude": -78.68}
FAR = {"latitude": 45.0, "longitude": -81.0}
NEAR_ID = "01JTESTSHAREDAREANEAR00001"
OTHER_ID = "01JTESTSHAREDAREAOTHER0001"
ITEM_ID = "01JTESTSHAREDSINGLEITEM001"
NAME_455 = "Highway 407 East of Bethesda"
NAME_456 = "Highway 407 near Darlington Clarke Townline Road"


def _area(
    subentry_id: str,
    title: str,
    location: dict[str, float],
    items: dict[str, str],
) -> ConfigSubentryDataWithId:
    return ConfigSubentryDataWithId(
        subentry_id=subentry_id,
        subentry_type=SUBENTRY_AREA,
        title=title,
        unique_id=None,
        data=area_data(
            location=location,
            radius_km=10.0,
            feeds=[Resource.ALERTS.value],
            **{CONF_ITEMS: {"cameras": items}},
        ),
    )


SINGLE_ITEM = ConfigSubentryDataWithId(
    subentry_id=ITEM_ID,
    subentry_type=SUBENTRY_ITEM,
    title=NAME_455,
    unique_id="cameras:455",
    data={CONF_RESOURCE: Resource.CAMERAS.value, CONF_ITEM_ID: "455"},
)
OVERLAPPING = [
    _area(NEAR_ID, "Near", SPOT, {"455": NAME_455}),
    _area(OTHER_ID, "Other", SPOT, {"456": NAME_456}),
    SINGLE_ITEM,
]


def _old_entry(subentries: list[ConfigSubentryDataWithId]) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        entry_id=ENTRY_ID,
        title="Ontario 511",
        data={CONF_API_KEY: API_KEY},
        unique_id=hashlib.sha256(API_KEY.encode()).hexdigest(),
        version=2,
        minor_version=2,
        subentries_data=subentries,
    )


def _old_device(
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
    entry: MockConfigEntry,
    subentry_id: str,
    item_id: str,
    object_id: str,
) -> tuple[dr.DeviceEntry, er.RegistryEntry]:
    """Register an item's device and sensor the way 0.1.0-alpha.2 did."""
    identifier = f"{subentry_id}:cameras:{item_id}"
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        config_subentry_id=subentry_id,
        identifiers={(DOMAIN, identifier)},
        name=object_id,
    )
    entity = entity_registry.async_get_or_create(
        Platform.SENSOR,
        DOMAIN,
        f"{identifier}:camera_road",
        config_entry=entry,
        config_subentry_id=subentry_id,
        device_id=device.id,
        suggested_object_id=object_id,
    )
    return device, entity


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)


def _item_device(
    device_registry: dr.DeviceRegistry, item_id: str
) -> dr.DeviceEntry | None:
    return device_registry.async_get_device_by_identifier(
        (DOMAIN, f"{ENTRY_ID}:cameras:{item_id}"), ENTRY_ID
    )


@pytest.mark.usefixtures("mock_api")
async def test_migration_keeps_ids_and_shares_items(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Devices move to the entry; IDs and settings stay; a duplicate is merged."""
    entry = _old_entry(OVERLAPPING)
    entry.add_to_hass(hass)
    duplicate, lost = _old_device(
        device_registry, entity_registry, entry, NEAR_ID, "455", "from_area"
    )
    renamed, kept = _old_device(
        device_registry, entity_registry, entry, ITEM_ID, "455", "from_item"
    )
    device_registry.async_update_device(renamed.id, name_by_user="My camera")
    other, other_entity = _old_device(
        device_registry, entity_registry, entry, OTHER_ID, "456", "other"
    )

    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert entry.version == 3
    moved = _item_device(device_registry, "455")
    assert moved is not None
    assert moved.id == renamed.id
    assert moved.name_by_user == "My camera"
    assert moved.config_subentry_id is None
    assert device_registry.async_get(duplicate.id) is None
    assert entity_registry.async_get(lost.entity_id) is None

    migrated = entity_registry.async_get(kept.entity_id)
    assert migrated is not None
    assert migrated.id == kept.id
    assert migrated.device_id == renamed.id
    assert migrated.config_subentry_id is None
    assert migrated.unique_id == f"{ENTRY_ID}:cameras:455:camera_road"

    other_moved = _item_device(device_registry, "456")
    assert other_moved is not None
    assert other_moved.id == other.id
    assert entity_registry.async_get(other_entity.entity_id) is not None

    issue = issue_registry.async_get_issue(DOMAIN, f"{MERGED_ITEMS_ISSUE}_{ENTRY_ID}")
    assert issue is not None
    assert issue.translation_placeholders == {"entities": lost.entity_id}

    # Both areas cover both cameras, so each now lists both.
    for subentry_id in (NEAR_ID, OTHER_ID):
        data = entry.subentries[subentry_id].data
        assert set(data[CONF_ITEMS]["cameras"]) == {"455", "456"}
        assert CONF_SHARE_ITEMS not in data


@pytest.mark.usefixtures("mock_api")
async def test_migration_does_not_bring_back_deleted_items(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """An item inside an area that nothing lists stays deleted."""
    entry = _old_entry([_area(NEAR_ID, "Near", SPOT, {"455": NAME_455})])
    entry.add_to_hass(hass)
    _old_device(device_registry, entity_registry, entry, NEAR_ID, "455", "kept")

    await _setup(hass, entry)

    assert set(entry.subentries[NEAR_ID].data[CONF_ITEMS]["cameras"]) == {"455"}
    assert _item_device(device_registry, "456") is None


@pytest.mark.usefixtures("mock_api")
async def test_migration_leaves_places_that_do_not_cover_an_item(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """An area far from another's item doesn't start listing it."""
    entry = _old_entry(
        [
            _area(NEAR_ID, "Near", SPOT, {"455": NAME_455}),
            _area(OTHER_ID, "Far", FAR, {"456": NAME_456}),
        ]
    )
    entry.add_to_hass(hass)
    _old_device(device_registry, entity_registry, entry, NEAR_ID, "455", "near")
    _old_device(device_registry, entity_registry, entry, OTHER_ID, "456", "far")

    await _setup(hass, entry)

    assert set(entry.subentries[NEAR_ID].data[CONF_ITEMS]["cameras"]) == {
        "455",
        "456",
    }
    # The far area doesn't cover the near area's camera.
    assert set(entry.subentries[OTHER_ID].data[CONF_ITEMS]["cameras"]) == {"456"}


@pytest.mark.usefixtures("mock_api")
async def test_migration_handles_an_emptied_type(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A type whose last item was deleted doesn't block generation."""
    area = _area(NEAR_ID, "Near", SPOT, {"455": NAME_455})
    entry = _old_entry(
        [
            ConfigSubentryDataWithId(
                subentry_id=NEAR_ID,
                subentry_type=SUBENTRY_AREA,
                title="Near",
                unique_id=None,
                data={
                    **area["data"],
                    CONF_ITEMS: {"cameras": {"455": NAME_455}, "roundabouts": {}},
                },
            ),
            ConfigSubentryDataWithId(
                subentry_id=OTHER_ID,
                subentry_type=SUBENTRY_AREA,
                title="New",
                unique_id=None,
                data=area_data(
                    location=SPOT,
                    radius_km=10.0,
                    feeds=[Resource.ALERTS.value],
                    pending=["cameras"],
                ),
            ),
        ]
    )
    entry.add_to_hass(hass)
    _old_device(device_registry, entity_registry, entry, NEAR_ID, "455", "near")

    await _setup(hass, entry)

    assert CONF_SHARE_ITEMS not in entry.subentries[NEAR_ID].data
    new = entry.subentries[OTHER_ID].data
    assert new[CONF_PENDING] == []
    assert set(new[CONF_ITEMS]["cameras"]) == {"455", "456"}


@pytest.mark.usefixtures("mock_api")
async def test_migration_leaves_other_integrations_entities_alone(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A helper linked to an item's device keeps its own unique ID."""
    entry = _old_entry([_area(NEAR_ID, "Near", SPOT, {"455": NAME_455})])
    entry.add_to_hass(hass)
    device, _ = _old_device(
        device_registry, entity_registry, entry, NEAR_ID, "455", "near"
    )
    helper_entry = MockConfigEntry(domain="template")
    helper_entry.add_to_hass(hass)
    helper = entity_registry.async_get_or_create(
        Platform.SENSOR,
        "template",
        "my_helper",
        config_entry=helper_entry,
        device_id=device.id,
    )

    await _setup(hass, entry)

    moved = entity_registry.async_get(helper.entity_id)
    assert moved is not None
    assert moved.unique_id == "my_helper"
    assert moved.device_id == device.id


@pytest.mark.usefixtures("mock_api")
async def test_migration_keeps_the_enabled_duplicate(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A disabled copy never wins over the one in use."""
    entry = _old_entry([_area(NEAR_ID, "Near", SPOT, {"455": NAME_455}), SINGLE_ITEM])
    entry.add_to_hass(hass)
    disabled, _ = _old_device(
        device_registry, entity_registry, entry, NEAR_ID, "455", "from_area"
    )
    device_registry.async_update_device(
        disabled.id,
        name_by_user="Disabled",
        disabled_by=dr.DeviceEntryDisabler.USER,
    )
    used, entity = _old_device(
        device_registry, entity_registry, entry, ITEM_ID, "455", "from_item"
    )

    await _setup(hass, entry)

    kept = _item_device(device_registry, "455")
    assert kept is not None
    assert kept.id == used.id
    assert entity_registry.async_get(entity.entity_id) is not None


@pytest.mark.usefixtures("mock_api")
async def test_migration_keeps_the_duplicate_with_a_renamed_entity(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A renamed entity counts as a customisation."""
    entry = _old_entry([_area(NEAR_ID, "Near", SPOT, {"455": NAME_455}), SINGLE_ITEM])
    entry.add_to_hass(hass)
    _old_device(device_registry, entity_registry, entry, NEAR_ID, "455", "first")
    used, entity = _old_device(
        device_registry, entity_registry, entry, ITEM_ID, "455", "second"
    )
    entity_registry.async_update_entity(
        entity.entity_id, new_entity_id="sensor.used_in_automation"
    )

    await _setup(hass, entry)

    kept = _item_device(device_registry, "455")
    assert kept is not None
    assert kept.id == used.id
    assert entity_registry.async_get("sensor.used_in_automation") is not None


@pytest.mark.usefixtures("mock_api")
async def test_migration_moves_an_entity_without_a_device(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
) -> None:
    """An item entity that lost its device link still gets the new unique ID."""
    entry = _old_entry([_area(NEAR_ID, "Near", SPOT, {"455": NAME_455})])
    entry.add_to_hass(hass)
    orphan = entity_registry.async_get_or_create(
        Platform.SENSOR,
        DOMAIN,
        f"{NEAR_ID}:cameras:455:camera_road",
        config_entry=entry,
        config_subentry_id=NEAR_ID,
        suggested_object_id="orphan",
    )

    await _setup(hass, entry)

    moved = entity_registry.async_get(orphan.entity_id)
    assert moved is not None
    assert moved.unique_id == f"{ENTRY_ID}:cameras:455:camera_road"


@pytest.mark.usefixtures("mock_api")
async def test_migration_fetches_each_resource_once(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Listing shared items waits for the first refresh instead of repeating it."""
    entry = _old_entry([_area(NEAR_ID, "Near", SPOT, {"455": NAME_455})])
    entry.add_to_hass(hass)
    _old_device(device_registry, entity_registry, entry, NEAR_ID, "455", "near")

    await _setup(hass, entry)

    urls = [str(call[1]).split("?")[0] for call in aioclient_mock.mock_calls]
    assert len(urls) == len(set(urls))


SHARED = [
    _area(NEAR_ID, "Near", SPOT, {"455": NAME_455, "456": NAME_456}),
    _area(OTHER_ID, "Other", SPOT, {"455": NAME_455}),
]


@pytest.mark.parametrize("subentries", [SHARED])
async def test_item_stays_until_nothing_lists_it(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Removing an area removes only the items no other place lists."""
    shared = _item_device(device_registry, "455")
    assert shared is not None
    assert _item_device(device_registry, "456") is not None

    hass.config_entries.async_remove_subentry(init_integration, NEAR_ID)
    await hass.async_block_till_done(wait_background_tasks=True)

    kept = _item_device(device_registry, "455")
    assert kept is not None
    assert kept.id == shared.id
    assert _item_device(device_registry, "456") is None


@pytest.mark.parametrize("subentries", [[*SHARED, SINGLE_ITEM]])
async def test_deleting_a_device_removes_it_everywhere(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Deleting a shared item's device drops it from every place listing it."""
    device = _item_device(device_registry, "455")
    assert device is not None

    assert await async_remove_config_entry_device(hass, init_integration, device)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert "455" not in init_integration.subentries[NEAR_ID].data[CONF_ITEMS]["cameras"]
    assert (
        "455" not in init_integration.subentries[OTHER_ID].data[CONF_ITEMS]["cameras"]
    )
    assert ITEM_ID not in init_integration.subentries


@pytest.mark.parametrize("subentries", [[*SHARED, SINGLE_ITEM]])
async def test_deleting_a_shared_device_reloads_once(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Dropping an item from three places is one reload."""
    device = _item_device(device_registry, "455")
    assert device is not None
    with patch.object(
        hass.config_entries,
        "async_schedule_reload",
        wraps=hass.config_entries.async_schedule_reload,
    ) as reload:
        assert await async_remove_config_entry_device(hass, init_integration, device)
        await hass.async_block_till_done(wait_background_tasks=True)
    reload.assert_called_once_with(init_integration.entry_id)


def _event(event_id: str) -> TrafficEvent:
    parsed = parse_items(Resource.EVENTS, load_json_array_fixture("event.json"))
    event = parsed[event_id]
    assert isinstance(event, TrafficEvent)
    return event


@pytest.mark.parametrize(
    ("subentry_ids", "expected"),
    [
        pytest.param([NEAR_ID], {"324420"}, id="one_place"),
        pytest.param([NEAR_ID, OTHER_ID], {"324420", "323209"}, id="both_places"),
        pytest.param(
            [NEAR_ID, ITEM_ID],
            {"324420", "323209", "322857"},
            id="single_item_has_no_limit",
        ),
        pytest.param([], {"324420", "323209", "322857"}, id="no_place"),
    ],
)
def test_segment_events_cover_the_places_listing_it(
    subentry_ids: list[str], expected: set[str]
) -> None:
    """A shared segment reports what's inside any area or commute listing it."""
    first, second, outside = _event("324420"), _event("323209"), _event("322857")
    index = FeedIndex(Mock())
    index._by_segment = {"segment": ([first, second, outside], [])}
    index._scopes = {
        NEAR_ID: Scope(centre=None, events=[(first, 1.0)]),
        OTHER_ID: Scope(centre=None, events=[(second, 1.0)]),
    }
    with patch.object(FeedIndex, "_rebuild_if_needed"):
        events, _ = index.on_segment("segment", subentry_ids)
    assert {e.id for e in events} == expected
