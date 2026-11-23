"""Tests for custom commute routes: pins and imported routes."""

from __future__ import annotations

import json
from typing import Any

from homeassistant.config_entries import (
    SOURCE_USER,
    ConfigSubentry,
    ConfigSubentryDataWithId,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, load_fixture
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.ontario_511.api import Resource
from custom_components.ontario_511.const import (
    CONF_CORRIDOR_KM,
    CONF_EDIT_ROUTE,
    CONF_FEEDS,
    CONF_FROM_AREA,
    CONF_PIN,
    CONF_PINS,
    CONF_REPLAN,
    CONF_ROUTE,
    CONF_ROUTE_TEXT,
    CONF_SEGMENTS,
    CONF_TO_AREA,
    CONF_TYPES,
    DOMAIN,
    SUBENTRY_AREA,
    SUBENTRY_COMMUTE,
)
from custom_components.ontario_511.custom_routes import (
    MAX_PINS,
    NamedStops,
    NoStops,
    RouteImportError,
    ShortLink,
    StopsOutsideOntario,
    UnsupportedRoute,
    insert_pin,
    parse_route,
    pins_from_route,
)
from custom_components.ontario_511.directions import ROUTE_URL
from custom_components.ontario_511.models import RoadSegment

from .common import EAST_401, KENORA, WEST_401
from .conftest import AREA_ID, ROUTE_PATTERN, area_data, serve_routes

START = (43.715, -79.60)
END = (43.77, -79.25)
VIA = (43.80, -79.40)
VIA_2 = (43.75, -79.50)
AREA_2_ID = "area_2"
START_DICT = {"latitude": START[0], "longitude": START[1]}
END_DICT = {"latitude": END[0], "longitude": END[1]}
TWO_AREAS = [
    ConfigSubentryDataWithId(
        subentry_id=AREA_ID,
        subentry_type=SUBENTRY_AREA,
        title="Home",
        unique_id=None,
        data=area_data(location=START_DICT, feeds=[Resource.ALERTS.value]),
    ),
    ConfigSubentryDataWithId(
        subentry_id=AREA_2_ID,
        subentry_type=SUBENTRY_AREA,
        title="Work",
        unique_id=None,
        data=area_data(location=END_DICT, feeds=[Resource.ALERTS.value]),
    ),
]
GPX_TRACK = """<?xml version="1.0"?>
<gpx version="1.1"><trk><trkseg>
<trkpt lat="43.715" lon="-79.60"><ele>100</ele></trkpt>
<trkpt lon="-79.40" lat="43.80"/>
<trkpt lat='43.77' lon='-79.25'/>
</trkseg></trk></gpx>"""
GPX_ROUTE = """<gpx><rte><rtept lat="43.80" lon="-79.40"/></rte>
<wpt lat="45.0" lon="-80.0"/></gpx>"""


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param(
            "https://www.google.com/maps/dir/?api=1&origin=43.715,-79.60"
            "&destination=43.77,-79.25&waypoints=43.80,-79.40%7C43.75,-79.50",
            [START, VIA, VIA_2, END],
            id="google_api_link",
        ),
        pytest.param(
            "https://www.google.ca/maps/dir/43.715,-79.60/43.80,-79.40/43.77,-79.25/"
            "@43.7,-79.4,11z/data=!4m2!4m1!3e0",
            [START, VIA, END],
            id="google_coordinates_in_path",
        ),
        pytest.param(
            "https://www.google.com/maps/dir/Home+St/43.80,-79.40/Work+Ave/"
            "@43.7,-79.4,11z/data=!4m8!1m2!1d-79.60!2d43.715!1m2!1d-79.25!2d43.77",
            [START, VIA, END],
            id="google_named_stops_with_coordinates_in_data",
        ),
        pytest.param(
            "https://maps.apple.com/?saddr=43.715,-79.60"
            "&daddr=43.80,-79.40+to:43.77,-79.25",
            [START, VIA, END],
            id="apple_saddr_daddr",
        ),
        pytest.param(
            "https://maps.apple.com/directions?source=43.715,-79.60"
            "&waypoint=43.80,-79.40&destination=43.77,-79.25&mode=driving",
            [START, VIA, END],
            id="apple_directions",
        ),
        pytest.param(GPX_TRACK, [START, VIA, END], id="gpx_track"),
        pytest.param(GPX_ROUTE, [VIA], id="gpx_route_before_waypoints"),
    ],
)
def test_parse_route(text: str, expected: list[tuple[float, float]]) -> None:
    """Links and GPX files give their stops in order."""
    assert parse_route(text) == expected


@pytest.mark.parametrize(
    ("text", "error"),
    [
        pytest.param("https://maps.app.goo.gl/abc123", ShortLink, id="google_short"),
        pytest.param("https://maps.apple/p/abc", ShortLink, id="apple_short"),
        pytest.param(
            "https://www.google.com/maps/dir/Home+St/Work+Ave/",
            NamedStops,
            id="named_without_coordinates",
        ),
        pytest.param(
            "https://maps.apple.com/?saddr=Home&daddr=Work",
            NamedStops,
            id="apple_named",
        ),
        pytest.param(
            "https://www.google.com/maps/dir/?api=1&origin=40.7,-74.0"
            "&destination=43.77,-79.25",
            StopsOutsideOntario,
            id="outside_ontario",
        ),
        pytest.param(
            "https://example.com/maps/dir/1,2/3,4", UnsupportedRoute, id="host"
        ),
        pytest.param(
            "https://google.com.example.org/maps/dir/43.7,-79.4/43.8,-79.3",
            UnsupportedRoute,
            id="lookalike_host",
        ),
        pytest.param("not a route", UnsupportedRoute, id="text"),
        pytest.param("<gpx></gpx>", UnsupportedRoute, id="empty_gpx"),
        pytest.param("", UnsupportedRoute, id="empty"),
    ],
)
def test_parse_route_errors(text: str, error: type[RouteImportError]) -> None:
    """What can't be read locally is refused, and never fetched."""
    with pytest.raises(error):
        parse_route(text)


def test_pins_leave_out_the_commutes_ends() -> None:
    """The commute keeps its own start and end."""
    assert pins_from_route([START, VIA, VIA, END], START, END) == [VIA]


def test_route_without_stops_between() -> None:
    """A link from the start straight to the end adds no pins."""
    with pytest.raises(NoStops):
        pins_from_route([START, END], START, END)


def test_long_track_is_reduced_to_spaced_pins() -> None:
    """A track of many points becomes at most the maximum number of pins."""
    track = [(43.7 + i * 0.001, -79.5 + i * 0.001) for i in range(500)]
    pins = pins_from_route(track, START, END)
    assert len(pins) == MAX_PINS
    assert pins == sorted(pins)
    assert pins[0] > track[0]
    assert pins[-1] < track[-1]


def test_pins_go_where_they_fit_along_the_trip() -> None:
    """A pin placed later can still belong earlier in the trip."""
    east = (43.76, -79.30)
    west = (43.72, -79.55)
    assert insert_pin([east], west, START, END) == [west, east]
    assert insert_pin([west], east, START, END) == [west, east]


def _serve_api(mock_api: AiohttpClientMocker) -> None:
    """Serve the fixtures, with road segments the test routes run along."""
    mock_api.clear_requests()
    for resource in Resource:
        if resource is not Resource.ROAD_CONDITIONS:
            mock_api.get(resource.url, text=load_fixture(f"{resource.value}.json"))
    mock_api.get(
        Resource.ROAD_CONDITIONS.url, text=json.dumps([WEST_401, EAST_401, KENORA])
    )


@pytest.fixture
async def commute(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_api: AiohttpClientMocker,
) -> ConfigSubentry:
    """Add a commute between the two areas."""
    _serve_api(mock_api)
    serve_routes(mock_api)
    configure = hass.config_entries.subentries.async_configure
    result = await hass.config_entries.subentries.async_init(
        (init_integration.entry_id, SUBENTRY_COMMUTE), context={"source": SOURCE_USER}
    )
    result = await configure(result["flow_id"], {"next_step_id": "existing_areas"})
    result = await configure(
        result["flow_id"], {CONF_FROM_AREA: AREA_ID, CONF_TO_AREA: AREA_2_ID}
    )
    result = await configure(
        result["flow_id"],
        {CONF_NAME: "Work run", CONF_CORRIDOR_KM: 5, CONF_TYPES: ["event"]},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done(wait_background_tasks=True)
    return next(
        s
        for s in init_integration.subentries.values()
        if s.subentry_type == SUBENTRY_COMMUTE
    )


def _route_calls(mock_api: AiohttpClientMocker) -> list[str]:
    return [
        str(call[1])
        for call in mock_api.mock_calls
        if str(call[1]).startswith(ROUTE_URL)
    ]


async def _open_route_menu(
    hass: HomeAssistant, entry: MockConfigEntry, commute: ConfigSubentry
) -> dict[str, Any]:
    result = await entry.start_subentry_reconfigure_flow(hass, commute.subentry_id)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_NAME: commute.title,
            CONF_FEEDS: list(commute.data[CONF_FEEDS]),
            CONF_CORRIDOR_KM: commute.data[CONF_CORRIDOR_KM],
            CONF_SEGMENTS: list(commute.data[CONF_SEGMENTS]),
            CONF_EDIT_ROUTE: True,
        },
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "route_pins"
    return result


async def _menu(hass: HomeAssistant, flow_id: str, step: str) -> dict[str, Any]:
    return await hass.config_entries.subentries.async_configure(
        flow_id, {"next_step_id": step}
    )


@pytest.mark.parametrize("subentries", [TWO_AREAS])
async def test_pins_are_routed_once_when_saved(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_api: AiohttpClientMocker,
    entity_registry: er.EntityRegistry,
    commute: ConfigSubentry,
) -> None:
    """Placing pins sends nothing; saving makes one request through them."""
    configure = hass.config_entries.subentries.async_configure
    before = len(_route_calls(mock_api))
    result = await _open_route_menu(hass, init_integration, commute)
    flow_id = result["flow_id"]
    for lat, lon in (VIA, VIA_2):
        result = await _menu(hass, flow_id, "route_pin_add")
        assert result["step_id"] == "route_pin_add"
        result = await configure(
            flow_id, {CONF_PIN: {"latitude": lat, "longitude": lon}}
        )
        assert result["step_id"] == "route_pins"
    assert len(_route_calls(mock_api)) == before

    result = await _menu(hass, flow_id, "route_save")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done(wait_background_tasks=True)

    (call,) = _route_calls(mock_api)[before:]
    assert "-79.50000,43.75000;-79.40000,43.80000" in call
    saved = init_integration.subentries[commute.subentry_id]
    assert saved.data[CONF_PINS] == [
        {"latitude": VIA_2[0], "longitude": VIA_2[1]},
        {"latitude": VIA[0], "longitude": VIA[1]},
    ]
    entity_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, f"{commute.subentry_id}:commute_route"
    )
    assert hass.states.get(entity_id).attributes[CONF_PINS] == [
        [VIA_2[0], VIA_2[1]],
        [VIA[0], VIA[1]],
    ]


@pytest.mark.parametrize("subentries", [TWO_AREAS])
async def test_import_replaces_the_pins(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    commute: ConfigSubentry,
) -> None:
    """An imported route's stops become the pins, without the commute's ends."""
    configure = hass.config_entries.subentries.async_configure
    result = await _open_route_menu(hass, init_integration, commute)
    flow_id = result["flow_id"]
    await _menu(hass, flow_id, "route_import")
    result = await configure(flow_id, {CONF_ROUTE_TEXT: GPX_TRACK})
    assert result["step_id"] == "route_pins"
    assert result["description_placeholders"]["count"] == "1"
    result = await _menu(hass, flow_id, "route_save")
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done(wait_background_tasks=True)
    assert init_integration.subentries[commute.subentry_id].data[CONF_PINS] == [
        {"latitude": VIA[0], "longitude": VIA[1]}
    ]


@pytest.mark.parametrize(
    ("text", "error"),
    [
        pytest.param("https://maps.app.goo.gl/abc", "import_short_link", id="short"),
        pytest.param(
            "https://www.google.com/maps/dir/Home/Work/",
            "import_named_stops",
            id="named",
        ),
        pytest.param(
            "https://maps.apple.com/?saddr=40.7,-74.0&daddr=43.77,-79.25",
            "import_outside_ontario",
            id="outside",
        ),
        pytest.param(
            "https://maps.apple.com/?saddr=43.715,-79.60&daddr=43.77,-79.25",
            "import_no_stops",
            id="no_stops",
        ),
        pytest.param("hello", "import_unsupported", id="unsupported"),
    ],
)
@pytest.mark.parametrize("subentries", [TWO_AREAS])
async def test_import_errors(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    commute: ConfigSubentry,
    text: str,
    error: str,
) -> None:
    """The form says why a pasted route can't be used."""
    result = await _open_route_menu(hass, init_integration, commute)
    flow_id = result["flow_id"]
    await _menu(hass, flow_id, "route_import")
    result = await hass.config_entries.subentries.async_configure(
        flow_id, {CONF_ROUTE_TEXT: text}
    )
    assert result["step_id"] == "route_import"
    assert result["errors"] == {CONF_ROUTE_TEXT: error}


@pytest.mark.parametrize("subentries", [TWO_AREAS])
async def test_pin_limits(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    commute: ConfigSubentry,
) -> None:
    """Pins must be in Ontario, and there's a maximum."""
    configure = hass.config_entries.subentries.async_configure
    result = await _open_route_menu(hass, init_integration, commute)
    flow_id = result["flow_id"]
    await _menu(hass, flow_id, "route_pin_add")
    result = await configure(
        flow_id, {CONF_PIN: {"latitude": 40.7, "longitude": -74.0}}
    )
    assert result["errors"] == {CONF_PIN: "pin_outside_ontario"}
    for index in range(MAX_PINS):
        result = await configure(
            flow_id,
            {CONF_PIN: {"latitude": 43.72 + index / 100, "longitude": -79.5}},
        )
        assert result["step_id"] == "route_pins"
        result = await _menu(hass, flow_id, "route_pin_add")
    result = await configure(
        flow_id, {CONF_PIN: {"latitude": 43.9, "longitude": -79.5}}
    )
    assert result["errors"] == {CONF_PIN: "too_many_pins"}


@pytest.mark.parametrize("subentries", [TWO_AREAS])
async def test_failed_route_keeps_the_current_one(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_api: AiohttpClientMocker,
    commute: ConfigSubentry,
) -> None:
    """If routing fails, the user can try again or keep the current route."""
    route = commute.data[CONF_ROUTE]
    configure = hass.config_entries.subentries.async_configure
    result = await _open_route_menu(hass, init_integration, commute)
    flow_id = result["flow_id"]
    await _menu(hass, flow_id, "route_pin_add")
    await configure(flow_id, {CONF_PIN: {"latitude": VIA[0], "longitude": VIA[1]}})
    _serve_api(mock_api)
    mock_api.get(ROUTE_PATTERN, status=503)
    result = await _menu(hass, flow_id, "route_save")
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "route_failed"
    result = await _menu(hass, flow_id, "route_keep")
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done(wait_background_tasks=True)
    saved = init_integration.subentries[commute.subentry_id]
    assert saved.data[CONF_ROUTE] == route
    assert CONF_PINS not in saved.data


@pytest.mark.parametrize("subentries", [TWO_AREAS])
async def test_plan_again_keeps_pins_until_removed(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_api: AiohttpClientMocker,
    commute: ConfigSubentry,
) -> None:
    """Planning again follows the pins; removing them all restores the default."""
    configure = hass.config_entries.subentries.async_configure
    result = await _open_route_menu(hass, init_integration, commute)
    flow_id = result["flow_id"]
    await _menu(hass, flow_id, "route_pin_add")
    await configure(flow_id, {CONF_PIN: {"latitude": VIA[0], "longitude": VIA[1]}})
    await _menu(hass, flow_id, "route_save")
    await hass.async_block_till_done(wait_background_tasks=True)
    commute = init_integration.subentries[commute.subentry_id]

    before = len(_route_calls(mock_api))
    result = await init_integration.start_subentry_reconfigure_flow(
        hass, commute.subentry_id
    )
    result = await configure(
        result["flow_id"],
        {
            CONF_NAME: commute.title,
            CONF_FEEDS: list(commute.data[CONF_FEEDS]),
            CONF_CORRIDOR_KM: commute.data[CONF_CORRIDOR_KM],
            CONF_SEGMENTS: list(commute.data[CONF_SEGMENTS]),
            CONF_REPLAN: True,
        },
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert result["reason"] == "reconfigure_successful"
    commute = init_integration.subentries[commute.subentry_id]
    assert len(commute.data[CONF_PINS]) == 1
    # The same request as before, so it's answered from the cache.
    assert len(_route_calls(mock_api)) == before

    result = await _open_route_menu(hass, init_integration, commute)
    flow_id = result["flow_id"]
    await _menu(hass, flow_id, "route_pin_remove")
    result = await configure(flow_id, {CONF_PINS: ["0"]})
    assert "route_pin_remove" not in result["menu_options"]
    await _menu(hass, flow_id, "route_save")
    await hass.async_block_till_done(wait_background_tasks=True)
    commute = init_integration.subentries[commute.subentry_id]
    assert CONF_PINS not in commute.data
    assert RoadSegment.from_api(WEST_401).id in commute.data[CONF_SEGMENTS]
