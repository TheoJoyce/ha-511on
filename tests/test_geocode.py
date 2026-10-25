"""Tests for address lookups (OpenStreetMap Nominatim) and their policy limits."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.ontario_511.geocode import (
    SEARCH_URL,
    SWITCH_URL,
    AddressNotFound,
    GeocodingError,
    GeocodingFailed,
    GeocodingUnavailable,
    OutsideOntario,
    get_geocoder,
)

TORONTO = [{"lat": "43.6532", "lon": "-79.3832", "display_name": "Toronto City Hall"}]
VANCOUVER = [{"lat": "49.28", "lon": "-123.12", "display_name": "Vancouver"}]


def _serve(
    aioclient_mock: AiohttpClientMocker,
    results: object = TORONTO,
    enabled: bool = True,
) -> None:
    aioclient_mock.get(SWITCH_URL, json={"enabled": enabled})
    aioclient_mock.get(SEARCH_URL, json=results)


def _calls(aioclient_mock: AiohttpClientMocker, url: str) -> list[tuple]:
    return [c for c in aioclient_mock.mock_calls if str(c[1]).startswith(url)]


@pytest.fixture(autouse=True)
def no_waiting() -> AsyncMock:
    """Record the one-second spacing instead of waiting (time is frozen)."""
    with patch(
        "custom_components.ontario_511.geocode.asyncio.sleep", new=AsyncMock()
    ) as sleep:
        yield sleep


async def test_lookup_identifies_the_integration(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A lookup checks the switch, then searches Canada as ha-511on."""
    _serve(aioclient_mock)
    place = await get_geocoder(hass, "0.1.0").async_lookup("  100 Queen St W, Toronto ")
    assert (place.latitude, place.longitude, place.name) == (
        43.6532,
        -79.3832,
        "Toronto City Hall",
    )
    (search,) = _calls(aioclient_mock, SEARCH_URL)
    assert search[1].query["q"] == "100 Queen St W, Toronto"
    assert search[1].query["countrycodes"] == "ca"
    assert search[3]["User-Agent"].startswith("ha-511on/0.1.0 (+https://github.com/")


async def test_repeated_addresses_use_the_cache(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The same address (in any spacing or case) is looked up once."""
    _serve(aioclient_mock)
    geocoder = get_geocoder(hass, "0.1.0")
    await geocoder.async_lookup("Toronto")
    await geocoder.async_lookup("  toronto ")
    assert len(_calls(aioclient_mock, SEARCH_URL)) == 1


async def test_lookups_are_spaced_a_second_apart(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, no_waiting: AsyncMock
) -> None:
    """A second lookup right after the first waits out the one-second limit."""
    _serve(aioclient_mock)
    geocoder = get_geocoder(hass, "0.1.0")
    await geocoder.async_lookup("Toronto")
    await geocoder.async_lookup("Toronto City Hall")
    no_waiting.assert_awaited_once_with(pytest.approx(1.0))


@pytest.mark.parametrize(
    "switch",
    [
        pytest.param({"json": {"enabled": False}}, id="switched_off"),
        pytest.param({"status": 404}, id="switch_missing"),
        pytest.param({"json": ["unexpected"]}, id="switch_malformed"),
    ],
)
async def test_switch_turns_lookups_off(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, switch: dict
) -> None:
    """If the switch says off, or can't be read, nothing is sent to Nominatim."""
    aioclient_mock.get(SWITCH_URL, **switch)
    aioclient_mock.get(SEARCH_URL, json=TORONTO)
    with pytest.raises(GeocodingUnavailable):
        await get_geocoder(hass, "0.1.0").async_lookup("Toronto")
    assert _calls(aioclient_mock, SEARCH_URL) == []


async def test_switch_is_checked_hourly(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    frozen_time: FrozenDateTimeFactory,
) -> None:
    """The switch is cached for an hour, then read again."""
    _serve(aioclient_mock)
    geocoder = get_geocoder(hass, "0.1.0")
    await geocoder.async_lookup("Toronto")
    await geocoder.async_lookup("Barrie")
    assert len(_calls(aioclient_mock, SWITCH_URL)) == 1
    frozen_time.tick(timedelta(hours=1, seconds=1))
    await geocoder.async_lookup("Sarnia")
    assert len(_calls(aioclient_mock, SWITCH_URL)) == 2


@pytest.mark.parametrize(
    ("search", "error"),
    [
        pytest.param({"json": []}, AddressNotFound, id="not_found"),
        pytest.param({"json": [{"lat": "x"}]}, AddressNotFound, id="malformed"),
        pytest.param({"json": VANCOUVER}, OutsideOntario, id="outside_ontario"),
        pytest.param({"status": 503}, GeocodingFailed, id="service_down"),
    ],
)
async def test_lookup_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    search: dict,
    error: type[GeocodingError],
) -> None:
    """Failures map to specific errors the form can explain."""
    aioclient_mock.get(SWITCH_URL, json={"enabled": True})
    aioclient_mock.get(SEARCH_URL, **search)
    with pytest.raises(error):
        await get_geocoder(hass, "0.1.0").async_lookup("Somewhere")
