"""Shared fixtures for Ontario 511 tests.

Tests never call the live API. Responses come from tests/fixtures/; see the
README there for where each file comes from.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
import hashlib
from unittest.mock import AsyncMock, patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, load_fixture
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.ontario_511.api import Resource
from custom_components.ontario_511.const import CONF_RADIUS, DOMAIN

API_KEY = "test-key-0123456789"
HOME = (43.6532, -79.3832)
FROZEN_NOW = datetime(2026, 10, 28, tzinfo=UTC)
FIXTURE_RESOURCES = (Resource.EVENTS, Resource.ALERTS)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading custom integrations in every test."""


@pytest.fixture(autouse=True)
def home_location(hass: HomeAssistant) -> None:
    """Put home in downtown Toronto, near the fixture events."""
    hass.config.latitude, hass.config.longitude = HOME


@pytest.fixture(autouse=True)
def frozen_time(freezer: FrozenDateTimeFactory) -> Generator[FrozenDateTimeFactory]:
    """Freeze time so event activity is deterministic."""
    freezer.move_to(FROZEN_NOW)
    yield freezer


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a config entry for the test key."""
    return MockConfigEntry(
        domain=DOMAIN,
        entry_id="01JTEST0NTAR10511ENTRY0001",
        title="Ontario 511",
        data={CONF_API_KEY: API_KEY},
        options={CONF_RADIUS: 25.0},
        unique_id=hashlib.sha256(API_KEY.encode()).hexdigest(),
    )


@pytest.fixture
def mock_api(aioclient_mock: AiohttpClientMocker) -> AiohttpClientMocker:
    """Serve the recorded fixtures for every resource the integration polls."""
    for resource in FIXTURE_RESOURCES:
        aioclient_mock.get(resource.url, text=load_fixture(f"{resource.value}.json"))
    return aioclient_mock


@pytest.fixture
async def init_integration(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_api: AiohttpClientMocker,
) -> MockConfigEntry:
    """Set up the integration with the recorded fixtures."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Stop config flow tests from setting up the entry they create."""
    with patch(
        "custom_components.ontario_511.async_setup_entry", return_value=True
    ) as mock:
        yield mock
