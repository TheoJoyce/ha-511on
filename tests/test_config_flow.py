"""Tests for the Ontario 511 config flow and subentry flows."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from homeassistant.components.zone import ATTR_RADIUS
from homeassistant.config_entries import (
    SOURCE_USER,
    ConfigSubentry,
    ConfigSubentryDataWithId,
)
from homeassistant.const import ATTR_LATITUDE, CONF_API_KEY, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import area_registry as ar, device_registry as dr
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.ontario_511.api import Resource
from custom_components.ontario_511.client import get_rate_limiter
from custom_components.ontario_511.const import (
    CONF_ADDRESS,
    CONF_CORRIDOR_KM,
    CONF_CREATE_ZONE,
    CONF_FEEDS,
    CONF_FROM_AREA,
    CONF_HA_AREA,
    CONF_ITEM_ID,
    CONF_LOCATION,
    CONF_PENDING,
    CONF_RADIUS_KM,
    CONF_RESOURCE,
    CONF_ROUTE,
    CONF_SEGMENTS,
    CONF_TO_AREA,
    CONF_TYPES,
    DOMAIN,
    SUBENTRY_AREA,
    SUBENTRY_COMMUTE,
    SUBENTRY_ITEM,
    VALIDATION_RESOURCE,
)
from custom_components.ontario_511.geocode import SEARCH_URL, SWITCH_URL
from custom_components.ontario_511.models import RoadSegment

from .common import EAST_401, KENORA, WEST_401
from .conftest import API_KEY, AREA_ID, area_data

INVALID_KEY_BODY = "<Error><Message>Invalid Key</Message></Error>"
# The west end of the synthetic Highway 401, and its east end.
WEST_END = {"latitude": 43.715, "longitude": -79.60}
EAST_END = {"latitude": 43.77, "longitude": -79.25}
HOME_INPUT: dict[str, Any] = {
    CONF_NAME: "Home",
    CONF_LOCATION: {**WEST_END, "radius": 12345},
    CONF_TYPES: ["event", "alerts", "roadconditions", "cameras"],
}
WORK_INPUT: dict[str, Any] = {
    CONF_NAME: "Work",
    CONF_LOCATION: {**EAST_END, "radius": 5000},
    CONF_TYPES: ["event"],
}
COMMUTE_INPUT: dict[str, Any] = {
    CONF_NAME: "Work run",
    CONF_CORRIDOR_KM: 5,
    CONF_TYPES: ["event", "cameras"],
}
WEST_ID = RoadSegment.from_api(WEST_401).id
EAST_ID = RoadSegment.from_api(EAST_401).id


def _mock_road_conditions(
    aioclient_mock: AiohttpClientMocker, segments: list[dict[str, Any]] | None = None
) -> None:
    aioclient_mock.get(
        Resource.ROAD_CONDITIONS.url,
        text=json.dumps([WEST_401, EAST_401, KENORA] if segments is None else segments),
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
    """Key, then an area, then finish."""
    result = await _start_with_key(hass, aioclient_mock)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], HOME_INPUT
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
    assert area.unique_id.startswith("area_")
    # The map's radius is in metres; it's stored in kilometres.
    assert area.data[CONF_RADIUS_KM] == 12.3
    assert area.data[CONF_FEEDS] == ["event", "alerts"]
    assert area.data[CONF_PENDING] == ["roadconditions", "cameras"]
    assert CONF_TYPES not in area.data
    assert aioclient_mock.call_count == 1


@pytest.mark.usefixtures("mock_setup_entry")
async def test_setup_with_commute(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A commute in setup adds a second area, then routes between the two."""
    _mock_road_conditions(aioclient_mock)
    configure = hass.config_entries.flow.async_configure
    result = await _start_with_key(hass, aioclient_mock)
    result = await configure(result["flow_id"], HOME_INPUT)
    result = await configure(result["flow_id"], {"next_step_id": "destination"})
    assert result["step_id"] == "area"
    result = await configure(result["flow_id"], WORK_INPUT)
    assert result["step_id"] == "commute"
    assert result["description_placeholders"] == {"from": "Home", "to": "Work"}

    result = await configure(result["flow_id"], COMMUTE_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    subentries = list(result["result"].subentries.values())
    home, work, commute = subentries
    assert [s.subentry_type for s in subentries] == [
        SUBENTRY_AREA,
        SUBENTRY_AREA,
        SUBENTRY_COMMUTE,
    ]
    assert commute.title == "Work run"
    assert commute.data[CONF_FROM_AREA] == home.unique_id
    assert commute.data[CONF_TO_AREA] == work.unique_id
    assert commute.data[CONF_SEGMENTS] == [WEST_ID, EAST_ID]
    assert {sid for sid, _ in commute.data[CONF_ROUTE]} == {WEST_ID, EAST_ID}
    assert commute.data[CONF_FEEDS] == ["event"]
    assert commute.data[CONF_PENDING] == ["cameras"]


@pytest.mark.usefixtures("mock_setup_entry")
async def test_local_commute_uses_a_straight_corridor(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """With no sensible highway route, the commute follows the straight line."""
    _mock_road_conditions(aioclient_mock, [KENORA])
    configure = hass.config_entries.flow.async_configure
    result = await _start_with_key(hass, aioclient_mock)
    result = await configure(result["flow_id"], HOME_INPUT)
    result = await configure(result["flow_id"], {"next_step_id": "destination"})
    result = await configure(result["flow_id"], WORK_INPUT)
    result = await configure(result["flow_id"], COMMUTE_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    commute = list(result["result"].subentries.values())[-1]
    assert commute.data[CONF_SEGMENTS] == []
    assert len(commute.data[CONF_ROUTE]) == 1


@pytest.mark.usefixtures("mock_setup_entry")
async def test_many_types_warn_about_slower_import(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """More than 10 data types shows a warning step before continuing."""
    configure = hass.config_entries.flow.async_configure
    result = await _start_with_key(hass, aioclient_mock)
    many = [r.value for r in Resource][:11]
    result = await configure(result["flow_id"], {**HOME_INPUT, CONF_TYPES: many})
    assert result["step_id"] == "slow_import"
    assert result["description_placeholders"] == {"fast": "10", "count": "11"}
    result = await configure(result["flow_id"], {})
    assert result["step_id"] == "add_commute"


async def test_area_requires_a_type(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """At least one data type is required."""
    result = await _start_with_key(hass, aioclient_mock)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**HOME_INPUT, CONF_TYPES: []}
    )
    assert result["step_id"] == "area"
    assert result["errors"] == {CONF_TYPES: "no_types"}


@pytest.mark.parametrize(
    "radius", [pytest.param(499, id="too_small"), pytest.param(50001, id="too_big")]
)
async def test_radius_out_of_range(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, radius: int
) -> None:
    """The radius must be 0.5 to 50 km; the form says so in metres."""
    result = await _start_with_key(hass, aioclient_mock)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {**HOME_INPUT, CONF_LOCATION: {**WEST_END, "radius": radius}},
    )
    assert result["step_id"] == "area"
    assert result["errors"] == {CONF_LOCATION: "radius_out_of_range"}
    assert result["description_placeholders"] == {
        "found": "",
        "min_radius": "500",
        "max_radius": "50000",
    }


@pytest.mark.usefixtures("mock_setup_entry")
async def test_commute_road_conditions_unavailable(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """If road conditions can't be fetched, the commute step shows an error."""
    aioclient_mock.get(Resource.ROAD_CONDITIONS.url, status=503)
    configure = hass.config_entries.flow.async_configure
    result = await _start_with_key(hass, aioclient_mock)
    result = await configure(result["flow_id"], HOME_INPUT)
    result = await configure(result["flow_id"], {"next_step_id": "destination"})
    result = await configure(result["flow_id"], WORK_INPUT)
    result = await configure(result["flow_id"], COMMUTE_INPUT)
    assert result["step_id"] == "commute"
    assert result["errors"] == {"base": "cannot_connect"}


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
        result["flow_id"],
        {
            **HOME_INPUT,
            CONF_NAME: "Cottage",
            CONF_LOCATION: {**WEST_END, "radius": 8000},
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    cottage = next(
        s for s in init_integration.subentries.values() if s.title == "Cottage"
    )
    assert cottage.unique_id.startswith("area_")
    assert cottage.data[CONF_RADIUS_KM] == 8


# A Home area at the west end of the synthetic 401, following events only.
WEST_AREA = [
    ConfigSubentryDataWithId(
        subentry_id=AREA_ID,
        subentry_type=SUBENTRY_AREA,
        title="Home",
        unique_id=None,
        data=area_data(location=WEST_END, feeds=[Resource.ALERTS.value]),
    )
]


@pytest.mark.parametrize("subentries", [WEST_AREA])
async def test_add_commute_to_a_new_area(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_api: AiohttpClientMocker,
) -> None:
    """A commute to a new area adds the area, then the commute."""
    mock_api.clear_requests()
    _mock_road_conditions(mock_api)
    configure = hass.config_entries.subentries.async_configure
    result = await _start_subentry(hass, init_integration, SUBENTRY_COMMUTE)
    assert result["type"] is FlowResultType.MENU
    result = await configure(result["flow_id"], {"next_step_id": "new_area"})
    result = await configure(result["flow_id"], {CONF_FROM_AREA: AREA_ID})
    assert result["step_id"] == "area"
    result = await configure(result["flow_id"], WORK_INPUT)
    assert result["step_id"] == "commute"
    result = await configure(result["flow_id"], COMMUTE_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY

    work = next(s for s in init_integration.subentries.values() if s.title == "Work")
    commute = next(
        s
        for s in init_integration.subentries.values()
        if s.subentry_type == SUBENTRY_COMMUTE
    )
    # Areas without a link key (from before) are linked by their subentry ID.
    assert commute.data[CONF_FROM_AREA] == AREA_ID
    assert commute.data[CONF_TO_AREA] == work.unique_id
    assert commute.data[CONF_SEGMENTS] == [WEST_ID, EAST_ID]

    result = await init_integration.start_subentry_reconfigure_flow(
        hass, commute.subentry_id
    )
    assert result["step_id"] == "reconfigure_commute"
    result = await configure(
        result["flow_id"],
        {
            CONF_NAME: "Work run",
            CONF_FEEDS: ["event"],
            CONF_CORRIDOR_KM: 2.5,
            CONF_SEGMENTS: [EAST_ID],
        },
    )
    assert result["reason"] == "reconfigure_successful"
    commute = init_integration.subentries[commute.subentry_id]
    assert commute.data[CONF_SEGMENTS] == [EAST_ID]
    assert commute.data[CONF_CORRIDOR_KM] == 2.5
    assert {sid for sid, _ in commute.data[CONF_ROUTE]} == {EAST_ID}


@pytest.mark.parametrize("subentries", [WEST_AREA])
async def test_add_commute_between_existing_areas(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_api: AiohttpClientMocker,
) -> None:
    """A commute can link two existing areas, but not an area to itself."""
    hass.config_entries.async_add_subentry(
        init_integration,
        ConfigSubentry(
            data=area_data(location=EAST_END, feeds=[]),
            subentry_type=SUBENTRY_AREA,
            title="Work",
            unique_id="area_work",
        ),
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    mock_api.clear_requests()
    _mock_road_conditions(mock_api)
    configure = hass.config_entries.subentries.async_configure
    result = await _start_subentry(hass, init_integration, SUBENTRY_COMMUTE)
    result = await configure(result["flow_id"], {"next_step_id": "existing_areas"})
    result = await configure(
        result["flow_id"], {CONF_FROM_AREA: AREA_ID, CONF_TO_AREA: AREA_ID}
    )
    assert result["errors"] == {CONF_TO_AREA: "same_area"}

    result = await configure(
        result["flow_id"], {CONF_FROM_AREA: AREA_ID, CONF_TO_AREA: "area_work"}
    )
    assert result["step_id"] == "commute"
    assert result["description_placeholders"] == {"from": "Home", "to": "Work"}
    result = await configure(result["flow_id"], COMMUTE_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.parametrize("subentries", [[]])
async def test_add_commute_needs_an_area(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Without any area there's nothing to start a commute from."""
    result = await _start_subentry(hass, init_integration, SUBENTRY_COMMUTE)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_areas"


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
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    area = init_integration.subentries[AREA_ID]
    assert area.title == "House"
    assert area.data[CONF_FEEDS] == ["constructionprojects"]


@pytest.mark.usefixtures("mock_setup_entry")
async def test_address_moves_the_pin(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An address is looked up and the map is shown again at the place found."""
    aioclient_mock.get(SWITCH_URL, json={"enabled": True})
    aioclient_mock.get(
        SEARCH_URL,
        json=[{"lat": "44.3894", "lon": "-79.6903", "display_name": "Barrie, Ontario"}],
    )
    result = await _start_with_key(hass, aioclient_mock)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**HOME_INPUT, CONF_ADDRESS: "Barrie"}
    )
    assert result["step_id"] == "area"
    assert result["errors"] == {}
    assert result["description_placeholders"]["found"] == "📍 Barrie, Ontario"
    schema = {key.schema: key for key in result["data_schema"].schema}
    assert schema[CONF_LOCATION].default() == {
        "latitude": 44.3894,
        "longitude": -79.6903,
        "radius": 12345,
    }
    assert schema[CONF_ADDRESS].default() == ""

    # Submitting the moved pin (no address) continues.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {**HOME_INPUT, CONF_LOCATION: schema[CONF_LOCATION].default()},
    )
    assert result["step_id"] == "add_commute"


@pytest.mark.parametrize(
    ("switch", "search", "error"),
    [
        pytest.param({"enabled": False}, [], "geocoding_unavailable", id="off"),
        pytest.param({"enabled": True}, [], "address_not_found", id="not_found"),
        pytest.param(
            {"enabled": True},
            [{"lat": "49.28", "lon": "-123.12", "display_name": "Vancouver"}],
            "outside_ontario",
            id="outside",
        ),
    ],
)
async def test_address_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    switch: dict[str, Any],
    search: list[dict[str, Any]],
    error: str,
) -> None:
    """Address problems are shown on the address field."""
    aioclient_mock.get(SWITCH_URL, json=switch)
    aioclient_mock.get(SEARCH_URL, json=search)
    result = await _start_with_key(hass, aioclient_mock)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**HOME_INPUT, CONF_ADDRESS: "Somewhere"}
    )
    assert result["step_id"] == "area"
    assert result["errors"] == {CONF_ADDRESS: error}


@pytest.mark.usefixtures("mock_setup_entry")
async def test_setup_with_zone_and_ha_area(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    area_registry: ar.AreaRegistry,
) -> None:
    """An area can create a matching zone and put its devices in an HA area."""
    outside = area_registry.async_create("Outside")
    result = await _start_with_key(hass, aioclient_mock)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {**HOME_INPUT, CONF_HA_AREA: outside.id, CONF_CREATE_ZONE: True},
    )
    # The zone is only created once the entry is.
    assert hass.states.get("zone.home_2") is None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    (area,) = result["result"].subentries.values()
    assert area.data[CONF_HA_AREA] == outside.id
    assert CONF_CREATE_ZONE not in area.data
    await hass.async_block_till_done()
    zone = hass.states.get("zone.home_2")
    assert zone.attributes[ATTR_LATITUDE] == WEST_END["latitude"]
    assert zone.attributes[ATTR_RADIUS] == 12300


@pytest.mark.usefixtures("mock_setup_entry")
async def test_commute_ha_area_defaults_to_start_area(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    area_registry: ar.AreaRegistry,
) -> None:
    """The commute's HA area is prefilled from the area it starts in."""
    outside = area_registry.async_create("Outside")
    _mock_road_conditions(aioclient_mock)
    configure = hass.config_entries.flow.async_configure
    result = await _start_with_key(hass, aioclient_mock)
    result = await configure(
        result["flow_id"], {**HOME_INPUT, CONF_HA_AREA: outside.id}
    )
    result = await configure(result["flow_id"], {"next_step_id": "destination"})
    result = await configure(result["flow_id"], WORK_INPUT)
    schema = {key.schema: key for key in result["data_schema"].schema}
    assert schema[CONF_HA_AREA].description == {"suggested_value": outside.id}


@pytest.mark.parametrize("subentries", [WEST_AREA])
async def test_reconfigure_moves_devices_to_ha_area(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    area_registry: ar.AreaRegistry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Choosing an HA area in settings moves every device of the area."""
    outside = area_registry.async_create("Outside")
    result = await init_integration.start_subentry_reconfigure_flow(hass, AREA_ID)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_NAME: "Home", CONF_FEEDS: ["alerts"], CONF_HA_AREA: outside.id},
    )
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done(wait_background_tasks=True)
    devices = dr.async_entries_for_config_entry(
        device_registry, init_integration.entry_id
    )
    assert devices
    assert {d.area_id for d in devices} == {outside.id}


async def test_reconfigure_item(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_api: AiohttpClientMocker,
    area_registry: ar.AreaRegistry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """A single item's settings only choose its Home Assistant area."""
    outside = area_registry.async_create("Outside")
    result = await _start_subentry(hass, init_integration, SUBENTRY_ITEM)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_RESOURCE: Resource.CAMERAS.value}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_ITEM_ID: "456"}
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    item = next(
        s
        for s in init_integration.subentries.values()
        if s.subentry_type == SUBENTRY_ITEM
    )

    result = await init_integration.start_subentry_reconfigure_flow(
        hass, item.subentry_id
    )
    assert list(result["data_schema"].schema) == [CONF_HA_AREA]
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_HA_AREA: outside.id}
    )
    assert result["reason"] == "reconfigure_successful"
    assert init_integration.subentries[item.subentry_id].data[CONF_HA_AREA] == (
        outside.id
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    (device,) = (
        d
        for d in dr.async_entries_for_config_entry(
            device_registry, init_integration.entry_id
        )
        if d.config_subentry_id == item.subentry_id
    )
    assert device.area_id == outside.id
