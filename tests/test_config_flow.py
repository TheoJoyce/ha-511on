"""Tests for the Ontario 511 config flow and subentry flows."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from homeassistant.config_entries import SOURCE_USER, ConfigSubentryDataWithId
from homeassistant.const import CONF_API_KEY, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.ontario_511.api import Resource
from custom_components.ontario_511.client import get_rate_limiter
from custom_components.ontario_511.const import (
    CONF_CORRIDOR_KM,
    CONF_END,
    CONF_EVENT_SLOTS,
    CONF_FEEDS,
    CONF_ITEM_ID,
    CONF_LOCATION,
    CONF_PENDING,
    CONF_RADIUS_KM,
    CONF_RESOURCE,
    CONF_ROUTE,
    CONF_SEGMENTS,
    CONF_START,
    CONF_TYPES,
    DOMAIN,
    SUBENTRY_AREA,
    SUBENTRY_COMMUTE,
    SUBENTRY_ITEM,
    VALIDATION_RESOURCE,
)
from custom_components.ontario_511.models import RoadSegment

from .common import EAST_401, KENORA, WEST_401
from .conftest import API_KEY, AREA_ID, HOME, area_data

INVALID_KEY_BODY = "<Error><Message>Invalid Key</Message></Error>"
HOME_LOCATION = {"latitude": HOME[0], "longitude": HOME[1]}
AREA_INPUT: dict[str, Any] = {
    CONF_NAME: "Home",
    CONF_LOCATION: HOME_LOCATION,
    CONF_RADIUS_KM: 20.5,
    CONF_TYPES: ["event", "alerts", "roadconditions", "cameras"],
    CONF_EVENT_SLOTS: 3,
}
COMMUTE_INPUT: dict[str, Any] = {
    CONF_NAME: "Work",
    CONF_START: {"latitude": 43.715, "longitude": -79.60},
    CONF_END: {"latitude": 43.77, "longitude": -79.25},
    CONF_CORRIDOR_KM: 5,
    CONF_TYPES: ["event", "cameras"],
    CONF_EVENT_SLOTS: 5,
}
WEST_ID = RoadSegment.from_api(WEST_401).id
EAST_ID = RoadSegment.from_api(EAST_401).id


def _mock_road_conditions(aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(
        Resource.ROAD_CONDITIONS.url,
        text=json.dumps([WEST_401, EAST_401, KENORA]),
    )


async def _start_with_key(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> dict[str, Any]:
    aioclient_mock.get(VALIDATION_RESOURCE.url, json=[])
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: f"  {API_KEY} "}
    )
    assert result["step_id"] == "area"
    return result


@pytest.mark.usefixtures("mock_setup_entry")
async def test_setup_with_area_only(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Key, then an area, then finish creates the entry with one area."""
    result = await _start_with_key(hass, aioclient_mock)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], AREA_INPUT
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "add_commute"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_API_KEY: API_KEY}
    entry = result["result"]
    assert entry.unique_id == hashlib.sha256(API_KEY.encode()).hexdigest()
    (area,) = entry.subentries.values()
    assert area.subentry_type == SUBENTRY_AREA
    assert area.title == "Home"
    assert area.data[CONF_RADIUS_KM] == 20.5
    assert area.data[CONF_FEEDS] == ["event", "alerts"]
    assert area.data[CONF_PENDING] == ["roadconditions", "cameras"]
    assert area.data[CONF_EVENT_SLOTS] == 3
    assert CONF_TYPES not in area.data
    assert aioclient_mock.call_count == 1


@pytest.mark.usefixtures("mock_setup_entry")
async def test_setup_with_commute(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A commute added during setup routes itself; no segments are asked for."""
    _mock_road_conditions(aioclient_mock)
    result = await _start_with_key(hass, aioclient_mock)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], AREA_INPUT
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "commute"}
    )
    assert result["step_id"] == "commute"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], COMMUTE_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    commute = next(
        s
        for s in result["result"].subentries.values()
        if s.subentry_type == SUBENTRY_COMMUTE
    )
    assert commute.title == "Work"
    assert commute.data[CONF_SEGMENTS] == [WEST_ID, EAST_ID]
    assert {sid for sid, _ in commute.data[CONF_ROUTE]} == {WEST_ID, EAST_ID}
    assert commute.data[CONF_FEEDS] == ["event"]
    assert commute.data[CONF_PENDING] == ["cameras"]


@pytest.mark.usefixtures("mock_setup_entry")
async def test_local_commute_uses_a_straight_corridor(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """With no sensible highway route, the commute follows the straight line."""
    aioclient_mock.get(Resource.ROAD_CONDITIONS.url, text=json.dumps([KENORA]))
    result = await _start_with_key(hass, aioclient_mock)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], AREA_INPUT
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "commute"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], COMMUTE_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    commute = next(
        s
        for s in result["result"].subentries.values()
        if s.subentry_type == SUBENTRY_COMMUTE
    )
    assert commute.data[CONF_SEGMENTS] == []
    assert len(commute.data[CONF_ROUTE]) == 1


@pytest.mark.usefixtures("mock_setup_entry")
async def test_many_types_warn_about_slower_import(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """More than 10 data types shows a warning step before continuing."""
    result = await _start_with_key(hass, aioclient_mock)
    many = [r.value for r in Resource][:11]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**AREA_INPUT, CONF_TYPES: many}
    )
    assert result["step_id"] == "slow_import"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "add_commute"


async def test_area_requires_a_type(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """At least one data type is required."""
    result = await _start_with_key(hass, aioclient_mock)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**AREA_INPUT, CONF_TYPES: []}
    )
    assert result["step_id"] == "area"
    assert result["errors"] == {CONF_TYPES: "no_types"}


async def test_radius_is_limited_by_the_form(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The radius field itself rejects more than 50 km."""
    result = await _start_with_key(hass, aioclient_mock)
    with pytest.raises(InvalidData):
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {**AREA_INPUT, CONF_RADIUS_KM: 60}
        )


@pytest.mark.parametrize(
    ("commute_input", "road_conditions", "errors"),
    [
        pytest.param(
            {CONF_END: COMMUTE_INPUT[CONF_START]},
            [],
            {CONF_END: "too_close"},
            id="too_close",
        ),
        pytest.param(
            {}, None, {"base": "cannot_connect"}, id="road_conditions_unavailable"
        ),
    ],
)
async def test_commute_validation(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    commute_input: dict[str, Any],
    road_conditions: list[dict[str, Any]] | None,
    errors: dict[str, str],
) -> None:
    """Commutes need distinct ends and some road segments between them."""
    if road_conditions is None:
        aioclient_mock.get(Resource.ROAD_CONDITIONS.url, status=503)
    else:
        aioclient_mock.get(
            Resource.ROAD_CONDITIONS.url, text=json.dumps(road_conditions)
        )
    result = await _start_with_key(hass, aioclient_mock)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], AREA_INPUT
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "commute"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**COMMUTE_INPUT, **commute_input}
    )
    assert result["step_id"] == "commute"
    assert result["errors"] == errors


@pytest.mark.parametrize(
    ("mock_kwargs", "error"),
    [
        pytest.param(
            {"status": 400, "text": INVALID_KEY_BODY}, "invalid_auth", id="auth"
        ),
        pytest.param({"status": 429}, "rate_limited", id="rate_limited"),
        pytest.param({"status": 503}, "cannot_connect", id="cannot_connect"),
        pytest.param({"exc": RuntimeError("boom")}, "unknown", id="unknown"),
    ],
)
async def test_key_errors_recover(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_kwargs: dict[str, Any],
    error: str,
) -> None:
    """Key errors are shown, and a later valid key continues to the area."""
    aioclient_mock.get(VALIDATION_RESOURCE.url, **mock_kwargs)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: API_KEY}
    )
    assert result["errors"] == {"base": error}

    aioclient_mock.clear_requests()
    aioclient_mock.get(VALIDATION_RESOURCE.url, json=[])
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: API_KEY}
    )
    assert result["step_id"] == "area"
    limiter = get_rate_limiter(hass)
    assert limiter.available == limiter.calls - 2


async def test_duplicate_key_aborts_without_api_call(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A key that is already configured is rejected before any call."""
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: API_KEY}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert aioclient_mock.call_count == 0


async def test_reauth_flow(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Reauthentication stores the new key and its unique ID."""
    new_key = "replacement-key-555"
    result = await init_integration.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    aioclient_mock.clear_requests()
    aioclient_mock.get(VALIDATION_RESOURCE.url, status=400, text=INVALID_KEY_BODY)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "bad"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    aioclient_mock.clear_requests()
    for resource in Resource:
        aioclient_mock.get(resource.url, json=[])
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: new_key}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert init_integration.data[CONF_API_KEY] == new_key
    assert init_integration.unique_id == hashlib.sha256(new_key.encode()).hexdigest()


async def _start_subentry(
    hass: HomeAssistant, entry: MockConfigEntry, subentry_type: str
) -> dict[str, Any]:
    return await hass.config_entries.subentries.async_init(
        (entry.entry_id, subentry_type), context={"source": SOURCE_USER}
    )


async def test_add_area(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Another area can be added from the integration page."""
    result = await _start_subentry(hass, init_integration, SUBENTRY_AREA)
    assert result["step_id"] == "area"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**AREA_INPUT, CONF_NAME: "Cottage"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    titles = {s.title for s in init_integration.subentries.values()}
    assert titles == {"Home", "Cottage"}


@pytest.mark.parametrize(
    "subentries",
    [
        [
            ConfigSubentryDataWithId(
                subentry_id=AREA_ID,
                subentry_type=SUBENTRY_AREA,
                title="Home",
                unique_id=None,
                data=area_data(feeds=[Resource.ALERTS.value]),
            )
        ]
    ],
)
async def test_add_commute(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_api: AiohttpClientMocker,
) -> None:
    """A commute can be added later, reusing the entry's client."""
    mock_api.clear_requests()
    _mock_road_conditions(mock_api)
    result = await _start_subentry(hass, init_integration, SUBENTRY_COMMUTE)
    assert result["step_id"] == "commute"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], COMMUTE_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    commute = next(
        s
        for s in init_integration.subentries.values()
        if s.subentry_type == SUBENTRY_COMMUTE
    )
    assert commute.data[CONF_SEGMENTS] == [WEST_ID, EAST_ID]

    result = await init_integration.start_subentry_reconfigure_flow(
        hass, commute.subentry_id
    )
    assert result["step_id"] == "reconfigure_commute"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Work",
            CONF_FEEDS: ["event"],
            CONF_EVENT_SLOTS: 2,
            CONF_CORRIDOR_KM: 2.5,
            CONF_SEGMENTS: [EAST_ID],
        },
    )
    assert result["reason"] == "reconfigure_successful"
    commute = init_integration.subentries[commute.subentry_id]
    assert commute.data[CONF_SEGMENTS] == [EAST_ID]
    assert commute.data[CONF_CORRIDOR_KM] == 2.5
    assert {sid for sid, _ in commute.data[CONF_ROUTE]} == {EAST_ID}


async def test_add_item(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_api: AiohttpClientMocker,
) -> None:
    """A single item is chosen from a list sorted by distance from home."""
    result = await _start_subentry(hass, init_integration, SUBENTRY_ITEM)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_RESOURCE: Resource.CAMERAS.value}
    )
    assert result["step_id"] == "pick"
    options = result["data_schema"].schema[CONF_ITEM_ID].config["options"]
    assert [o["value"] for o in options] == ["455", "456"]

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_ITEM_ID: "456"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    item = next(
        s
        for s in init_integration.subentries.values()
        if s.subentry_type == SUBENTRY_ITEM
    )
    assert item.title == "Highway 407 near Darlington Clarke Townline Road"
    assert item.unique_id == "cameras:456"

    result = await _start_subentry(hass, init_integration, SUBENTRY_ITEM)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_RESOURCE: Resource.CAMERAS.value}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_ITEM_ID: "456"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_add_item_connection_error(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_api: AiohttpClientMocker,
) -> None:
    """If the list can't be fetched, the flow stops with a clear reason."""
    mock_api.clear_requests()
    mock_api.get(Resource.FERRY_TERMINALS.url, status=503)
    result = await _start_subentry(hass, init_integration, SUBENTRY_ITEM)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_RESOURCE: Resource.FERRY_TERMINALS.value}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_reconfigure_area(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """An area can be renamed and change what it follows."""
    result = await init_integration.start_subentry_reconfigure_flow(hass, AREA_ID)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "House",
            CONF_FEEDS: ["constructionprojects"],
            CONF_EVENT_SLOTS: 0,
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    area = init_integration.subentries[AREA_ID]
    assert area.title == "House"
    assert area.data[CONF_FEEDS] == ["constructionprojects"]
    assert area.data[CONF_EVENT_SLOTS] == 0
