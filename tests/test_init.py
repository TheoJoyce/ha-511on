"""Tests for setting up, migrating and generating items."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigEntryState,
    ConfigSubentryDataWithId,
)
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, load_fixture
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.ontario_511.api import Resource
from custom_components.ontario_511.const import (
    CONF_CORRIDOR_KM,
    CONF_END,
    CONF_EVENT_SLOTS,
    CONF_FEEDS,
    CONF_ITEMS,
    CONF_PENDING,
    CONF_RADIUS_KM,
    CONF_SEGMENTS,
    CONF_START,
    DOMAIN,
    SUBENTRY_AREA,
    SUBENTRY_COMMUTE,
)
from custom_components.ontario_511.models import RoadSegment

from .common import road_condition
from .conftest import API_KEY, AREA_ID, area_data

INVALID_KEY = "<Error><Message>Invalid Key</Message></Error>"
# The two documented camera samples, on Highway 407 near Bowmanville.
CAMERA_SPOT = {"latitude": 43.995, "longitude": -78.68}
COMMUTE_ID = "01JTEST0NTAR10511COMMUTE01"


async def test_setup_and_unload(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The entry loads and unloads cleanly."""
    assert init_integration.state is ConfigEntryState.LOADED
    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()
    assert init_integration.state is ConfigEntryState.NOT_LOADED


async def test_invalid_key_starts_reauth(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A rejected key fails setup and asks the user for a new one."""
    for resource in Resource:
        aioclient_mock.get(resource.url, status=400, text=INVALID_KEY)
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_connection_error_retries_setup(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """An unreachable API defers setup."""
    for resource in Resource:
        aioclient_mock.get(resource.url, status=503)
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_migrate_from_first_alpha(
    hass: HomeAssistant, mock_api: AiohttpClientMocker
) -> None:
    """A version 1 entry becomes a Home area with the same radius."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_API_KEY: API_KEY},
        options={"radius": 10.0},
        unique_id=hashlib.sha256(API_KEY.encode()).hexdigest(),
        version=1,
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.version == 2
    assert entry.options == {}
    (area,) = entry.subentries.values()
    assert area.subentry_type == SUBENTRY_AREA
    assert area.title == "Home"
    assert area.data[CONF_RADIUS_KM] == 10.0
    assert area.data[CONF_FEEDS] == ["event", "alerts"]


@pytest.mark.parametrize(
    "subentries",
    [
        [
            ConfigSubentryDataWithId(
                subentry_id=AREA_ID,
                subentry_type=SUBENTRY_AREA,
                title="Highway 407 east",
                unique_id=None,
                data=area_data(
                    location=CAMERA_SPOT,
                    radius_km=10.0,
                    feeds=[],
                    pending=["cameras", "ferryterminals"],
                ),
            )
        ]
    ],
)
async def test_area_generation(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_api: AiohttpClientMocker,
) -> None:
    """Pending types generate items once, then are forgotten."""
    area = init_integration.subentries[AREA_ID]
    assert area.data[CONF_PENDING] == []
    assert area.data[CONF_ITEMS] == {
        "cameras": {
            "455": "Highway 407 East of Bethesda",
            "456": "Highway 407 near Darlington Clarke Townline Road",
        }
    }

    calls = mock_api.call_count
    assert await hass.config_entries.async_reload(init_integration.entry_id)
    await hass.async_block_till_done()
    assert (
        init_integration.subentries[AREA_ID].data[CONF_ITEMS] == area.data[CONF_ITEMS]
    )
    assert mock_api.call_count == calls


def _route_near_cameras() -> dict[str, Any]:
    return road_condition(
        "Bowmanville to Clarington",
        [(43.98, -78.75), (43.995, -78.685), (44.005, -78.62)],
        road="407",
    )


@pytest.mark.parametrize(
    "subentries",
    [
        [
            ConfigSubentryDataWithId(
                subentry_id=AREA_ID,
                subentry_type=SUBENTRY_AREA,
                title="Bowmanville",
                unique_id=None,
                data=area_data(
                    location=CAMERA_SPOT,
                    radius_km=10.0,
                    feeds=[],
                    pending=["cameras"],
                ),
            ),
            ConfigSubentryDataWithId(
                subentry_id=COMMUTE_ID,
                subentry_type=SUBENTRY_COMMUTE,
                title="Work",
                unique_id=None,
                data={
                    CONF_START: {"latitude": 43.98, "longitude": -78.75},
                    CONF_END: {"latitude": 44.005, "longitude": -78.62},
                    CONF_SEGMENTS: [RoadSegment.from_api(_route_near_cameras()).id],
                    CONF_CORRIDOR_KM: 5.0,
                    CONF_FEEDS: [],
                    CONF_PENDING: ["cameras"],
                    CONF_ITEMS: {},
                    CONF_EVENT_SLOTS: 5,
                },
            ),
        ]
    ],
)
async def test_commute_items_take_priority(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Items along a commute go to the commute, not to an overlapping area."""
    aioclient_mock.get(
        Resource.ROAD_CONDITIONS.url, text=json.dumps([_route_near_cameras()])
    )
    aioclient_mock.get(Resource.CAMERAS.url, text=load_fixture("cameras.json"))
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    commute = mock_config_entry.subentries[COMMUTE_ID]
    area = mock_config_entry.subentries[AREA_ID]
    assert set(commute.data[CONF_ITEMS]["cameras"]) == {"455", "456"}
    assert "cameras" not in area.data[CONF_ITEMS]
    assert commute.data[CONF_PENDING] == area.data[CONF_PENDING] == []
