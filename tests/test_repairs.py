"""Tests for planning commutes again, along roads: the repair for old
commutes, and the option in a commute's settings."""

from __future__ import annotations

from typing import Any

from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.config_entries import ConfigSubentryDataWithId
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.ontario_511.const import (
    CONF_CORRIDOR_KM,
    CONF_END,
    CONF_FEEDS,
    CONF_FROM_AREA,
    CONF_ITEMS,
    CONF_PENDING,
    CONF_REPLAN,
    CONF_ROUTE,
    CONF_ROUTING,
    CONF_SEGMENTS,
    CONF_START,
    CONF_TO_AREA,
    DOMAIN,
    REPLAN_ISSUE,
    ROUTING_ROADS,
    SUBENTRY_AREA,
    SUBENTRY_COMMUTE,
)
from custom_components.ontario_511.geo import encode_polyline
from custom_components.ontario_511.osm import SWITCH_URL

from .conftest import AREA_ID, ROUTE_PATTERN, area_data, serve_routes
from .test_config_flow import EAST_END, WEST_END

COMMUTE_ID = "01JTEST0NTAR10511COMMUTE01"
ISSUE_ID = f"{REPLAN_ISSUE}_{COMMUTE_ID}"
# Planned by 0.1.0-alpha.1: highways only, every route merged, no CONF_ROUTING.
OLD_COMMUTE = {
    CONF_FROM_AREA: AREA_ID,
    CONF_TO_AREA: "area_work",
    CONF_START: {"latitude": 43.6, "longitude": -79.7},
    CONF_END: {"latitude": 43.8, "longitude": -79.2},
    CONF_SEGMENTS: [],
    CONF_ROUTE: [["", encode_polyline([(43.6, -79.7), (43.8, -79.2)])]],
    CONF_CORRIDOR_KM: 5.0,
    CONF_FEEDS: [],
    CONF_PENDING: [],
    CONF_ITEMS: {"cameras": {"455": "Highway 401 camera"}},
}
SUBENTRIES = [
    ConfigSubentryDataWithId(
        subentry_id=AREA_ID,
        subentry_type=SUBENTRY_AREA,
        title="Home",
        unique_id=None,
        data=area_data(location=WEST_END, feeds=[]),
    ),
    ConfigSubentryDataWithId(
        subentry_id="01JTEST0NTAR10511AREA00002",
        subentry_type=SUBENTRY_AREA,
        title="Work",
        unique_id="area_work",
        data=area_data(location=EAST_END, feeds=[]),
    ),
    ConfigSubentryDataWithId(
        subentry_id=COMMUTE_ID,
        subentry_type=SUBENTRY_COMMUTE,
        title="Work run",
        unique_id=None,
        data=OLD_COMMUTE,
    ),
]


async def _start_fix(hass: HomeAssistant) -> dict:
    assert await async_setup_component(hass, "repairs", {})
    manager = repairs_flow_manager(hass)
    assert manager is not None
    return await manager.async_init(DOMAIN, context={"issue_id": ISSUE_ID})


@pytest.mark.parametrize("subentries", [SUBENTRIES])
async def test_old_commute_is_planned_again(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_api: AiohttpClientMocker,
) -> None:
    """An old commute gets a repair; fixing it plans its routes along roads."""
    issue = ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID)
    assert issue is not None
    assert issue.is_fixable
    assert issue.translation_placeholders == {"commute": "Work run"}

    # Nothing is sent to OpenStreetMap until the user confirms.
    serve_routes(mock_api)
    result = await _start_fix(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"
    assert not [c for c in mock_api.mock_calls if ROUTE_PATTERN.match(str(c[1]))]

    result = await repairs_flow_manager(hass).async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done(wait_background_tasks=True)
    commute = init_integration.subentries[COMMUTE_ID]
    assert commute.data[CONF_ROUTING] == ROUTING_ROADS
    # Planned between the areas' centres, and its item types generate again.
    assert commute.data[CONF_START] == WEST_END
    assert commute.data[CONF_END] == EAST_END
    assert "455" in commute.data[CONF_ITEMS]["cameras"]
    assert commute.data[CONF_PENDING] == []
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None


@pytest.mark.parametrize(
    ("switch", "route", "error"),
    [
        pytest.param({"routing": False}, {"json": {}}, "routing_unavailable", id="off"),
        pytest.param({"routing": True}, {"status": 503}, "routing_failed", id="down"),
    ],
)
@pytest.mark.parametrize("subentries", [SUBENTRIES])
async def test_fix_when_routing_fails(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_api: AiohttpClientMocker,
    switch: dict[str, bool],
    route: dict[str, Any],
    error: str,
) -> None:
    """Without routing, the commute stays as it was and the repair stays."""
    mock_api.get(SWITCH_URL, json=switch)
    mock_api.get(ROUTE_PATTERN, **route)
    result = await _start_fix(hass)
    result = await repairs_flow_manager(hass).async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}
    assert init_integration.subentries[COMMUTE_ID].data == OLD_COMMUTE
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is not None


@pytest.mark.parametrize("subentries", [SUBENTRIES])
async def test_issue_goes_with_the_commute(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Deleting the commute, or the entry, removes its repair."""
    hass.config_entries.async_remove_subentry(init_integration, COMMUTE_ID)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None


@pytest.mark.parametrize("subentries", [SUBENTRIES])
async def test_issue_goes_with_the_entry(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Removing the integration removes its repairs."""
    await hass.config_entries.async_remove(init_integration.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None


@pytest.mark.parametrize(
    "subentries",
    [
        [
            *SUBENTRIES[:2],
            ConfigSubentryDataWithId(
                subentry_id=COMMUTE_ID,
                subentry_type=SUBENTRY_COMMUTE,
                title="Work run",
                unique_id=None,
                data={**OLD_COMMUTE, CONF_ROUTING: ROUTING_ROADS},
            ),
        ]
    ],
)
async def test_commutes_planned_along_roads_get_no_repair(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Only commutes planned before routes followed roads need planning again."""
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None


@pytest.mark.parametrize("subentries", [SUBENTRIES])
async def test_settings_plan_when_routing_is_off(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_api: AiohttpClientMocker,
) -> None:
    """With routing switched off, the settings say so and keep the route."""
    serve_routes(mock_api, routing=False)
    result = await init_integration.start_subentry_reconfigure_flow(hass, COMMUTE_ID)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Work run",
            CONF_FEEDS: [],
            CONF_CORRIDOR_KM: 5,
            CONF_SEGMENTS: [],
            CONF_REPLAN: True,
        },
    )
    assert result["errors"] == {"base": "routing_unavailable"}
    assert init_integration.subentries[COMMUTE_ID].data == OLD_COMMUTE


@pytest.mark.parametrize("subentries", [SUBENTRIES])
async def test_settings_plan_saves_new_routes(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_api: AiohttpClientMocker,
) -> None:
    """Planning again from the settings stores routes along roads."""
    serve_routes(mock_api)
    result = await init_integration.start_subentry_reconfigure_flow(hass, COMMUTE_ID)
    await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Work run",
            CONF_FEEDS: [],
            CONF_CORRIDOR_KM: 2.5,
            CONF_SEGMENTS: [],
            CONF_REPLAN: True,
        },
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    commute = init_integration.subentries[COMMUTE_ID]
    assert commute.data[CONF_ROUTING] == ROUTING_ROADS
    assert commute.data[CONF_CORRIDOR_KM] == 2.5
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None
