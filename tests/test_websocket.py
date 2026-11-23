"""Tests for the WebSocket command behind the map card's scopes."""

from __future__ import annotations

from homeassistant.config_entries import (
    ConfigEntryDisabler,
    ConfigSubentryDataWithId,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.ontario_511.api import Resource
from custom_components.ontario_511.commute import plan_highway_commute
from custom_components.ontario_511.const import (
    CONF_CORRIDOR_KM,
    CONF_END,
    CONF_FEEDS,
    CONF_FROM_AREA,
    CONF_ITEMS,
    CONF_PENDING,
    CONF_RESOURCE,
    CONF_START,
    CONF_TO_AREA,
    DOMAIN,
    SUBENTRY_AREA,
    SUBENTRY_COMMUTE,
    SUBENTRY_ITEM,
)
from custom_components.ontario_511.models import RoadSegment

from .common import ALONG_401, EAST_401, WEST_401
from .conftest import AREA_ID, area_data

WORK_ID = "work_area"
COMMUTE_ID = "commute_1"
START = {"latitude": ALONG_401[0][0], "longitude": ALONG_401[0][1]}
END = {"latitude": ALONG_401[-1][0], "longitude": ALONG_401[-1][1]}
SCOPED = [
    ConfigSubentryDataWithId(
        subentry_id=AREA_ID,
        subentry_type=SUBENTRY_AREA,
        title="Home",
        unique_id=None,
        data=area_data(location=START, feeds=[Resource.ALERTS.value]),
    ),
    ConfigSubentryDataWithId(
        subentry_id=WORK_ID,
        subentry_type=SUBENTRY_AREA,
        title="Work",
        unique_id=None,
        data=area_data(location=END, feeds=[Resource.ALERTS.value]),
    ),
    ConfigSubentryDataWithId(
        subentry_id=COMMUTE_ID,
        subentry_type=SUBENTRY_COMMUTE,
        title="Work run",
        unique_id=None,
        data={
            CONF_FROM_AREA: AREA_ID,
            CONF_TO_AREA: WORK_ID,
            CONF_START: START,
            CONF_END: END,
            CONF_CORRIDOR_KM: 5.0,
            CONF_FEEDS: [Resource.ALERTS.value],
            CONF_PENDING: [],
            CONF_ITEMS: {},
            **plan_highway_commute(
                [RoadSegment.from_api(WEST_401), RoadSegment.from_api(EAST_401)],
                ALONG_401[0],
                ALONG_401[-1],
            ),
        },
    ),
    ConfigSubentryDataWithId(
        subentry_id="single_item",
        subentry_type=SUBENTRY_ITEM,
        title="One camera",
        unique_id="cameras:1",
        data={CONF_RESOURCE: Resource.CAMERAS.value, "item_id": "1"},
    ),
]


@pytest.mark.parametrize("subentries", [SCOPED])
async def test_map_scopes(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    entity_registry: er.EntityRegistry,
    init_integration: MockConfigEntry,
) -> None:
    """Areas and commutes are returned with their geometry; items aren't."""
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": f"{DOMAIN}/map_scopes"})
    response = await client.receive_json()
    assert response["success"]
    home, work, commute = response["result"]["scopes"]
    assert home["type"] == work["type"] == "area"
    assert home["center"] == [START["latitude"], START["longitude"]]
    assert home["radius_km"] == init_integration.subentries[AREA_ID].data["radius_km"]
    assert commute["type"] == "commute"
    assert commute["id"] == COMMUTE_ID
    assert commute["corridor_km"] == 5.0
    assert commute["route_entity"] == entity_registry.async_get_entity_id(
        "sensor", DOMAIN, f"{COMMUTE_ID}:commute_route"
    )
    assert commute["lines"]
    assert [area["center"] for area in commute["areas"]] == [
        home["center"],
        work["center"],
    ]


@pytest.mark.parametrize("subentries", [SCOPED])
async def test_map_scopes_survive_a_reload(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    init_integration: MockConfigEntry,
) -> None:
    """A card asking while the entry reloads still gets its scopes."""
    await hass.config_entries.async_unload(init_integration.entry_id)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": f"{DOMAIN}/map_scopes"})
    response = await client.receive_json()
    assert len(response["result"]["scopes"]) == 3


@pytest.mark.parametrize("subentries", [SCOPED])
async def test_map_scopes_skip_disabled_entries(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    init_integration: MockConfigEntry,
) -> None:
    """A disabled entry has nothing on the map."""
    await hass.config_entries.async_set_disabled_by(
        init_integration.entry_id, ConfigEntryDisabler.USER
    )
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": f"{DOMAIN}/map_scopes"})
    response = await client.receive_json()
    assert response["result"] == {"scopes": []}
