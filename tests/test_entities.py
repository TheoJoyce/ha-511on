"""Tests for Ontario 511 entities.

The test area holds one item of every type, plus two synthetic road segments
along Highway 401 in Toronto, where the fixture's real events are.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
import json
from typing import Any
from unittest.mock import PropertyMock, patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.camera import async_get_image
from homeassistant.components.homeassistant import (
    DOMAIN as HOMEASSISTANT_DOMAIN,
    SERVICE_UPDATE_ENTITY,
)
from homeassistant.config_entries import ConfigSubentryDataWithId
from homeassistant.const import (
    ATTR_ATTRIBUTION,
    ATTR_ENTITY_ID,
    ATTR_LATITUDE,
    STATE_OFF,
    STATE_ON,
    STATE_UNKNOWN,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    load_fixture,
    load_json_array_fixture,
    snapshot_platform,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from syrupy.assertion import SnapshotAssertion

from custom_components.ontario_511 import async_remove_config_entry_device
from custom_components.ontario_511.api import Resource
from custom_components.ontario_511.const import (
    ATTRIBUTION,
    CONF_ITEMS,
    DOMAIN,
    ITEM_RESOURCES,
    SUBENTRY_AREA,
)
from custom_components.ontario_511.coordinator import parse_items
from custom_components.ontario_511.models import RoadSegment

from .common import EAST_401, KENORA, WEST_401
from .conftest import AREA_ID, SimulatedClock, area_data

WEST_ID = RoadSegment.from_api(WEST_401).id
CAMERA_URL = "https://on.stage.traveliq.co/map/Cctv/815"


def _first_items() -> dict[str, dict[str, str]]:
    """Return the first fixture item of every item resource, as stored data."""
    items: dict[str, dict[str, str]] = {}
    for resource in ITEM_RESOURCES:
        if resource is Resource.ROAD_CONDITIONS:
            continue
        parsed = parse_items(
            resource, load_json_array_fixture(f"{resource.value}.json")
        )
        first = next(iter(parsed.values()))
        items[resource.value] = {first.id: first.name}
    items[Resource.ROAD_CONDITIONS.value] = {
        RoadSegment.from_api(s).id: RoadSegment.from_api(s).name
        for s in (WEST_401, EAST_401)
    }
    return items


AREA = ConfigSubentryDataWithId(
    subentry_id=AREA_ID,
    subentry_type=SUBENTRY_AREA,
    title="Home",
    unique_id=None,
    data=area_data(
        feeds=["event", "constructionprojects", "alerts"],
        event_slots=3,
        items=_first_items(),
    ),
)

pytestmark = pytest.mark.parametrize("subentries", [[AREA]])


def _serve(aioclient_mock: AiohttpClientMocker, events: list[dict[str, Any]]) -> None:
    for resource in Resource:
        if resource is Resource.ROAD_CONDITIONS:
            text = json.dumps([WEST_401, EAST_401, KENORA])
        elif resource is Resource.EVENTS:
            text = json.dumps(events)
        else:
            text = load_fixture(f"{resource.value}.json")
        aioclient_mock.get(resource.url, text=text)


@pytest.fixture
def events() -> list[dict[str, Any]]:
    """Return the events the mock API serves."""
    return load_json_array_fixture("event.json")


@pytest.fixture
async def loaded(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    events: list[dict[str, Any]],
) -> MockConfigEntry:
    """Set up the entry with every type of item."""
    _serve(aioclient_mock, events)
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return mock_config_entry


@pytest.mark.parametrize(
    "platform",
    [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.EVENT, Platform.CAMERA],
)
async def test_entities(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    events: list[dict[str, Any]],
    platform: Platform,
) -> None:
    """Every platform's entities and states match the snapshot."""
    _serve(aioclient_mock, events)
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.ontario_511.PLATFORMS", [platform]),
        patch(
            "homeassistant.helpers.entity.Entity.entity_registry_enabled_default",
            new_callable=PropertyMock,
            return_value=True,
        ),
        # Camera access tokens are random; pin them so snapshots are stable.
        patch("random.SystemRandom.getrandbits", return_value=123456789),
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)


async def test_every_entity_has_attribution_and_subentry(
    hass: HomeAssistant, entity_registry: er.EntityRegistry, loaded: MockConfigEntry
) -> None:
    """Every entity carries the OGL-Ontario attribution and its subentry."""
    entries = er.async_entries_for_config_entry(entity_registry, loaded.entry_id)
    assert entries
    for entry in entries:
        assert entry.config_subentry_id == AREA_ID
        if entry.disabled_by is not None:
            continue
        state = hass.states.get(entry.entity_id)
        assert state is not None
        assert state.attributes.get(ATTR_ATTRIBUTION) == ATTRIBUTION


@pytest.mark.usefixtures("loaded")
async def test_road_segment_and_summary(hass: HomeAssistant) -> None:
    """Events attach to their segment and count towards the area."""
    assert hass.states.get("sensor.hwy_401_west_road_condition").state == "good"
    assert hass.states.get("sensor.hwy_401_west_events").state == "2"
    assert hass.states.get("binary_sensor.hwy_401_west_full_closure").state == STATE_ON
    assert hass.states.get("binary_sensor.hwy_401_west_hazardous").state == STATE_OFF
    assert hass.states.get("sensor.home_events").state == "3"
    assert hass.states.get("sensor.home_worst_road_condition").state == "good"
    assert hass.states.get("binary_sensor.home_full_closure").state == STATE_ON
    assert hass.states.get("binary_sensor.home_hazardous_conditions").state == STATE_OFF
    # Only the documented alert listing the Central region applies here.
    assert hass.states.get("sensor.home_alerts").state == "1"


@pytest.mark.usefixtures("loaded")
async def test_nearest_events(hass: HomeAssistant) -> None:
    """The three nearest active events, with their location for the map."""
    first = hass.states.get("sensor.home_nearest_event_1")
    third = hass.states.get("sensor.home_nearest_event_3")
    assert first.attributes["event_id"] == "323209"
    assert first.attributes[ATTR_LATITUDE] == pytest.approx(43.729862)
    assert third.attributes["event_id"] == "324420"
    assert first.attributes["distance_km"] <= third.attributes["distance_km"]


async def test_nearest_events_unknown_without_events(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """With fewer than three events, the rest are Unknown, not unavailable."""
    _serve(aioclient_mock, [])
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("sensor.home_nearest_event_1").state == STATE_UNKNOWN
    assert hass.states.get("sensor.home_events").state == "0"


async def test_new_event_fires(
    hass: HomeAssistant,
    loaded: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    events: list[dict[str, Any]],
    frozen_time: FrozenDateTimeFactory,
) -> None:
    """A new event on the segment fires the segment's and area's event entities."""
    assert hass.states.get("event.home_new_event").state == STATE_UNKNOWN
    new_event = {**events[0], "ID": 999999, "Description": "New collision"}
    aioclient_mock.clear_requests()
    _serve(aioclient_mock, [*events, new_event])

    frozen_time.tick(timedelta(minutes=3, seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    for entity_id in ("event.home_new_event", "event.hwy_401_west_new_event"):
        state = hass.states.get(entity_id)
        assert state.state != STATE_UNKNOWN
        assert state.attributes["event_id"] == "999999"
        assert state.attributes["description"] == "New collision"


async def test_update_entity_reuses_fresh_data(
    hass: HomeAssistant, loaded: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """Home Assistant's "Update entity" action doesn't call the API while fresh."""
    await async_setup_component(hass, HOMEASSISTANT_DOMAIN, {})
    calls = aioclient_mock.call_count
    await hass.services.async_call(
        HOMEASSISTANT_DOMAIN,
        SERVICE_UPDATE_ENTITY,
        {ATTR_ENTITY_ID: "sensor.home_events"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == calls


async def test_camera_image_is_cached(
    hass: HomeAssistant,
    loaded: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    frozen_time: FrozenDateTimeFactory,
) -> None:
    """A camera image is fetched once a minute at most."""
    aioclient_mock.get(CAMERA_URL, content=b"jpeg-bytes")
    camera = next(
        e.entity_id
        for e in er.async_entries_for_config_entry(er.async_get(hass), loaded.entry_id)
        if e.domain == Platform.CAMERA
    )

    def image_calls() -> int:
        return sum(1 for c in aioclient_mock.mock_calls if str(c[1]) == CAMERA_URL)

    assert (await async_get_image(hass, camera)).content == b"jpeg-bytes"
    await async_get_image(hass, camera)
    assert image_calls() == 1

    frozen_time.tick(timedelta(seconds=61))
    await async_get_image(hass, camera)
    assert image_calls() == 2


async def test_removing_an_item_device(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
    loaded: MockConfigEntry,
) -> None:
    """Deleting an item's device drops it from the area; summaries stay."""
    item_device = device_registry.async_get_device_by_identifier(
        (DOMAIN, f"{AREA_ID}:roadconditions:{WEST_ID}"), loaded.entry_id
    )
    summary_device = device_registry.async_get_device_by_identifier(
        (DOMAIN, f"{AREA_ID}:summary"), loaded.entry_id
    )
    assert item_device is not None
    assert summary_device is not None

    assert not await async_remove_config_entry_device(hass, loaded, summary_device)
    assert await async_remove_config_entry_device(hass, loaded, item_device)
    await hass.async_block_till_done()
    items = loaded.subentries[AREA_ID].data[CONF_ITEMS]
    assert WEST_ID not in items["roadconditions"]


async def test_more_than_ten_resources_wait_for_the_rate_limit(
    loaded: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    limiter_clock: SimulatedClock,
) -> None:
    """All 16 resources load, the last six after the limiter's 61 s window."""
    assert len(loaded.runtime_data.coordinators) == 16
    assert aioclient_mock.call_count == 16
    assert limiter_clock.waits == [pytest.approx(61.0)]
    assert all(c.data is not None for c in loaded.runtime_data.coordinators.values())


async def test_late_events_data_does_not_fire_new_event(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    events: list[dict[str, Any]],
) -> None:
    """Events that load after the entities exist aren't reported as new.

    Regression test: the entity used to record an empty baseline before the
    events arrived, then report every existing event as new.
    """
    release = asyncio.Event()

    async def held_events(
        method: str, url: Any, data: Any
    ) -> AiohttpClientMockResponse:
        await release.wait()
        return AiohttpClientMockResponse(method, url, text=json.dumps(events))

    for resource in Resource:
        if resource is Resource.EVENTS:
            aioclient_mock.get(resource.url, side_effect=held_events)
        elif resource is Resource.ROAD_CONDITIONS:
            aioclient_mock.get(
                resource.url, text=json.dumps([WEST_401, EAST_401, KENORA])
            )
        else:
            aioclient_mock.get(
                resource.url, text=load_fixture(f"{resource.value}.json")
            )
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("event.home_new_event") is not None

    release.set()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("sensor.home_events").state == "3"
    assert hass.states.get("event.home_new_event").state == STATE_UNKNOWN
    assert hass.states.get("event.hwy_401_west_new_event").state == STATE_UNKNOWN
