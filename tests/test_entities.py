"""Tests for Ontario 511 entities."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import ATTR_ATTRIBUTION, STATE_OFF, STATE_ON, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    snapshot_platform,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)
from syrupy.assertion import SnapshotAssertion

from custom_components.ontario_511.api import Resource
from custom_components.ontario_511.const import ATTRIBUTION, CONF_RADIUS, DOMAIN

NEARBY_EVENTS = "sensor.ontario_511_nearby_events"
ACTIVE_ALERTS = "sensor.ontario_511_active_alerts"
NEARBY_CLOSURE = "binary_sensor.ontario_511_nearby_road_closure"


@pytest.mark.parametrize("platform", [Platform.SENSOR, Platform.BINARY_SENSOR])
async def test_registered_entities(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    mock_config_entry: MockConfigEntry,
    mock_api: AiohttpClientMocker,
    platform: Platform,
) -> None:
    """Registered entities and their states match the snapshot."""
    mock_config_entry.add_to_hass(hass)
    with patch("custom_components.ontario_511.PLATFORMS", [platform]):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)


@pytest.mark.usefixtures("init_integration")
async def test_geo_location_entities(
    hass: HomeAssistant, snapshot: SnapshotAssertion
) -> None:
    """Only active, nearby events with valid coordinates appear on the map."""
    states = sorted(
        hass.states.async_all(Platform.GEO_LOCATION), key=lambda s: s.entity_id
    )
    assert states == snapshot


@pytest.mark.usefixtures("init_integration")
async def test_every_entity_has_attribution(hass: HomeAssistant) -> None:
    """The OGL-Ontario attribution is on every entity."""
    states = [
        state
        for state in hass.states.async_all()
        if state.domain
        in (Platform.SENSOR, Platform.BINARY_SENSOR, Platform.GEO_LOCATION)
    ]
    assert states
    assert {state.attributes.get(ATTR_ATTRIBUTION) for state in states} == {ATTRIBUTION}


async def test_events_follow_feed_and_radius(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_api: AiohttpClientMocker,
    frozen_time: FrozenDateTimeFactory,
) -> None:
    """Geolocation entities and counts update as the feed changes."""
    assert hass.states.get(NEARBY_EVENTS).state == "3"
    assert hass.states.get(NEARBY_CLOSURE).state == STATE_ON
    assert len(hass.states.async_all(Platform.GEO_LOCATION)) == 3

    mock_api.clear_requests()
    mock_api.get(Resource.EVENTS.url, json=[])
    mock_api.get(Resource.ALERTS.url, json=[])
    frozen_time.tick(timedelta(minutes=5, seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert hass.states.get(NEARBY_EVENTS).state == "0"
    assert hass.states.get(NEARBY_CLOSURE).state == STATE_OFF
    assert hass.states.get(ACTIVE_ALERTS).state == "0"
    assert hass.states.async_all(Platform.GEO_LOCATION) == []


@pytest.mark.parametrize(
    ("radius", "expected"),
    [
        pytest.param(5.0, "0", id="5km"),
        pytest.param(13.0, "2", id="13km"),
        pytest.param(25.0, "3", id="25km"),
    ],
)
async def test_radius_option(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_api: AiohttpClientMocker,
    radius: float,
    expected: str,
) -> None:
    """Only events within the configured radius count as nearby."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_RADIUS: radius}
    )
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get(NEARBY_EVENTS).state == expected
    assert len(hass.states.async_all(Platform.GEO_LOCATION)) == int(expected)


@pytest.mark.usefixtures("init_integration")
async def test_alerts_sensor(hass: HomeAssistant) -> None:
    """Alerts are counted and listed, high importance first."""
    state = hass.states.get(ACTIVE_ALERTS)
    assert state.state == "2"
    assert [a["high_importance"] for a in state.attributes["alerts"]] == [True, False]
    assert hass.states.get(NEARBY_EVENTS).attributes.get("alerts") is None
    assert DOMAIN in state.entity_id


async def test_malformed_events_are_tolerated(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Items without an ID or usable coordinates don't break the update."""
    aioclient_mock.get(
        Resource.EVENTS.url,
        json=[
            {"RoadwayName": "HWY 401", "Latitude": 43.7, "Longitude": -79.4},
            {"ID": 1, "Latitude": None, "Longitude": None, "StartDate": 0},
            {"ID": 2, "Latitude": "43.7", "Longitude": -79.4},
            {"ID": 3, "Latitude": 43.7, "Longitude": -79.4},
        ],
    )
    aioclient_mock.get(Resource.ALERTS.url, json=[])
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get(NEARBY_EVENTS).state == "1"
    assert [s.name for s in hass.states.async_all(Platform.GEO_LOCATION)] == ["Event 3"]
