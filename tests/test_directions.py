"""Tests for road routing (OpenStreetMap's OSRM) and its policy limits."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.ontario_511.directions import (
    ROUTE_URL,
    RoutingFailed,
    RoutingUnavailable,
    async_get_router,
)
from custom_components.ontario_511.osm import SWITCH_URL

from .common import ALONG_401, osrm_response
from .conftest import ROUTE_PATTERN

START, END = ALONG_401[0], ALONG_401[-1]
DETOUR = [START, (43.80, -79.40), END]


def _calls(aioclient_mock: AiohttpClientMocker, url: str) -> list[tuple]:
    return [c for c in aioclient_mock.mock_calls if str(c[1]).startswith(url)]


async def test_routes_identify_the_integration(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A request checks the switch, then asks for full road routes, fastest first."""
    aioclient_mock.get(SWITCH_URL, json={"routing": True})
    aioclient_mock.get(
        ROUTE_PATTERN, json=osrm_response([(DETOUR, 30.0), (ALONG_401, 20.0)])
    )
    router = await async_get_router(hass)
    routes = await router.async_routes([START, END], alternatives=True)
    assert [r.minutes for r in routes] == [20.0, 30.0]
    assert routes[0].line == tuple(ALONG_401)
    (call,) = _calls(aioclient_mock, ROUTE_URL)
    # Longitude first, as OSRM expects.
    assert call[1].path.endswith("/-79.60000,43.71500;-79.25000,43.77000")
    assert call[1].query["overview"] == "full"
    assert call[1].query["alternatives"] == "3"
    assert call[3]["User-Agent"].startswith("ha-511on/")


async def test_repeated_routes_use_the_cache(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, routing_spacing: AsyncMock
) -> None:
    """The same request is sent once; different ones are a second apart."""
    aioclient_mock.get(SWITCH_URL, json={"routing": True})
    aioclient_mock.get(ROUTE_PATTERN, json=osrm_response([(ALONG_401, 20.0)]))
    router = await async_get_router(hass)
    await router.async_routes([START, END])
    await router.async_routes([START, END])
    routing_spacing.assert_not_awaited()
    await router.async_routes([START, (43.80, -79.40), END])
    assert len(_calls(aioclient_mock, ROUTE_URL)) == 2
    routing_spacing.assert_awaited_once_with(pytest.approx(1.0))


@pytest.mark.parametrize(
    "switch",
    [
        pytest.param({"json": {"enabled": True, "routing": False}}, id="off"),
        # Address search on doesn't turn routing on.
        pytest.param({"json": {"enabled": True}}, id="no_routing_key"),
        pytest.param({"status": 404}, id="switch_missing"),
    ],
)
async def test_switch_turns_routing_off(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, switch: dict
) -> None:
    """If the switch doesn't say on, nothing is sent to the routing service."""
    aioclient_mock.get(SWITCH_URL, **switch)
    aioclient_mock.get(ROUTE_PATTERN, json=osrm_response([(ALONG_401, 20.0)]))
    with pytest.raises(RoutingUnavailable):
        await (await async_get_router(hass)).async_routes([START, END])
    assert _calls(aioclient_mock, ROUTE_URL) == []


@pytest.mark.parametrize(
    "response",
    [
        pytest.param({"status": 503}, id="service_down"),
        pytest.param({"status": 400, "json": {"code": "NoRoute"}}, id="no_route"),
        pytest.param({"json": {"code": "Ok", "routes": []}}, id="no_routes"),
        pytest.param({"json": {"routes": [{"geometry": 1}]}}, id="malformed"),
    ],
)
async def test_routing_errors(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, response: dict
) -> None:
    """Anything but usable routes is a failure the form can explain."""
    aioclient_mock.get(SWITCH_URL, json={"routing": True})
    aioclient_mock.get(ROUTE_PATTERN, **response)
    with pytest.raises(RoutingFailed):
        await (await async_get_router(hass)).async_routes([START, END])
