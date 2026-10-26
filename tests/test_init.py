"""Tests for setting up, migrating and generating items."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigEntryState,
    ConfigSubentryDataWithId,
)
from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, load_fixture
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.ontario_511.api import Resource
from custom_components.ontario_511.card import frontend_hash
from custom_components.ontario_511.const import (
    CONF_CORRIDOR_KM,
    CONF_END,
    CONF_FEEDS,
    CONF_HA_AREA,
    CONF_ITEMS,
    CONF_PENDING,
    CONF_RADIUS_KM,
    CONF_ROUTE,
    CONF_SEGMENTS,
    CONF_START,
    DOMAIN,
    SUBENTRY_AREA,
    SUBENTRY_COMMUTE,
)
from custom_components.ontario_511.models import RoadSegment

from .common import encode_polyline, road_condition
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
                title="Home",
                unique_id=None,
                data=area_data(event_slots=5),
            )
        ]
    ],
)
async def test_migrate_event_slots(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_api: AiohttpClientMocker,
    entity_registry: er.EntityRegistry,
) -> None:
    """Version 2.1 slot settings go; slots beyond the nearest three are removed."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_config_entry, minor_version=1)
    for slot in range(1, 6):
        entity_registry.async_get_or_create(
            Platform.SENSOR,
            DOMAIN,
            f"{AREA_ID}:nearest_event_{slot}",
            config_entry=mock_config_entry,
            config_subentry_id=AREA_ID,
            suggested_object_id=f"home_nearest_event_{slot}",
        )
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert mock_config_entry.minor_version == 2
    assert "event_slots" not in mock_config_entry.subentries[AREA_ID].data
    assert hass.states.get("sensor.home_nearest_event_3") is not None
    assert entity_registry.async_get("sensor.home_nearest_event_4") is None
    assert entity_registry.async_get("sensor.home_nearest_event_5") is None


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


async def test_new_devices_go_in_the_chosen_ha_area(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_api: AiohttpClientMocker,
    area_registry: ar.AreaRegistry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Devices created for an area start in its Home Assistant area."""
    outside = area_registry.async_create("Outside")
    mock_config_entry.add_to_hass(hass)
    subentry = mock_config_entry.subentries[AREA_ID]
    hass.config_entries.async_update_subentry(
        mock_config_entry,
        subentry,
        data={**subentry.data, CONF_HA_AREA: outside.id},
    )
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    devices = dr.async_entries_for_config_entry(
        device_registry, mock_config_entry.entry_id
    )
    assert devices
    assert {d.area_id for d in devices} == {outside.id}


ROUTE_LINE = [(43.7, -79.6), (43.7, -79.5)]
# Local roads from the start to the highway, about 1.4 km.
LOCAL_LINE = [(43.71, -79.61), (43.7, -79.6)]


@pytest.mark.parametrize(
    "subentries",
    [
        [
            ConfigSubentryDataWithId(
                subentry_id=COMMUTE_ID,
                subentry_type=SUBENTRY_COMMUTE,
                title="Work",
                unique_id=None,
                data={
                    CONF_START: {"latitude": 43.7, "longitude": -79.6},
                    CONF_END: {"latitude": 43.7, "longitude": -79.5},
                    CONF_SEGMENTS: [],
                    # Two routes sharing a stretch count it once.
                    CONF_ROUTE: [
                        ["", encode_polyline(LOCAL_LINE)],
                        ["a", encode_polyline(ROUTE_LINE)],
                        ["a", encode_polyline(ROUTE_LINE)],
                    ],
                    CONF_CORRIDOR_KM: 5.0,
                    CONF_FEEDS: [],
                    CONF_PENDING: [],
                    CONF_ITEMS: {},
                },
            )
        ]
    ],
)
async def test_commute_route_sensor(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_api: AiohttpClientMocker,
) -> None:
    """A commute's Route sensor has its length and the lines for the map card.

    Highway stretches and local roads are kept apart, so the card can draw
    local roads (straight lines) differently.
    """
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    state = hass.states.get("sensor.work_route")
    assert float(state.state) == pytest.approx(8.0 + 1.4, abs=0.1)
    assert state.attributes["unit_of_measurement"] == "km"
    assert state.attributes["route"] == [[[43.7, -79.6], [43.7, -79.5]]]
    assert state.attributes["local"] == [[[43.71, -79.61], [43.7, -79.6]]]


async def test_map_card_is_served(hass: HomeAssistant) -> None:
    """The card's files are served from a URL with their hash, and it's loaded.

    Browsers cache the files for a month, so changed files need a new URL.
    """
    hass.config.components.add("frontend")
    hass.http = Mock(async_register_static_paths=AsyncMock())
    with patch("custom_components.ontario_511.card.add_extra_js_url") as add_js:
        assert await async_setup_component(hass, DOMAIN, {})
    (paths,) = hass.http.async_register_static_paths.call_args.args
    (path,) = paths
    url = f"/ontario_511/{frontend_hash()}"
    assert path.url_path == url
    assert path.cache_headers
    assert (Path(path.path) / "maplibre-gl.mjs").is_file()
    add_js.assert_called_once_with(hass, f"{url}/ontario-511-map-card.js")


def test_card_url_changes_with_its_files(tmp_path: Path) -> None:
    """Changing any of the card's files changes its URL."""
    (tmp_path / "ontario-511-map-card.js").write_text("one")
    with patch("custom_components.ontario_511.card.FRONTEND_DIR", tmp_path):
        before = frontend_hash()
        (tmp_path / "ontario-511-map-card.js").write_text("two")
        assert frontend_hash() != before
