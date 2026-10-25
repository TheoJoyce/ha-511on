"""Shared fixtures for Ontario 511 tests.

Tests never call the live API. Responses come from tests/fixtures/; see the
README there for where each file comes from.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
import hashlib
from typing import Any
from unittest.mock import AsyncMock, patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigSubentryDataWithId
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, load_fixture
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.ontario_511.api import RateLimiter, Resource
from custom_components.ontario_511.client import RATE_LIMITER
from custom_components.ontario_511.const import (
    CONF_EVENT_SLOTS,
    CONF_FEEDS,
    CONF_ITEMS,
    CONF_LOCATION,
    CONF_PENDING,
    CONF_RADIUS_KM,
    DOMAIN,
    SUBENTRY_AREA,
)

API_KEY = "test-key-0123456789"
HOME = (43.6532, -79.3832)
FROZEN_NOW = datetime(2026, 10, 28, tzinfo=UTC)
ENTRY_ID = "01JTEST0NTAR10511ENTRY0001"
AREA_ID = "01JTEST0NTAR10511AREA00001"


def area_data(**overrides: Any) -> dict[str, Any]:
    """Return subentry data for a 25 km area around home."""
    return {
        CONF_LOCATION: {"latitude": HOME[0], "longitude": HOME[1]},
        CONF_RADIUS_KM: 25.0,
        CONF_FEEDS: [Resource.EVENTS.value, Resource.ALERTS.value],
        CONF_PENDING: [],
        CONF_ITEMS: {},
        CONF_EVENT_SLOTS: 5,
        **overrides,
    }


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


class SimulatedClock:
    """A monotonic clock for the rate limiter that moves only when it sleeps.

    Tests freeze time, which would also freeze the limiter's clock, so a test
    using more than 10 resources would wait forever for the window to slide.
    This keeps the limit fully enforced while simulating the wait.
    """

    def __init__(self) -> None:
        """Start at zero."""
        self.now = 0.0
        self.waits: list[float] = []

    def __call__(self) -> float:
        """Return the simulated time."""
        return self.now

    async def sleep(self, seconds: float) -> None:
        """Record the wait and advance simulated time."""
        self.waits.append(seconds)
        self.now += seconds


@pytest.fixture(autouse=True)
def limiter_clock(hass: HomeAssistant) -> SimulatedClock:
    """Install the instance-wide rate limiter with a simulated clock."""
    clock = SimulatedClock()
    hass.data[RATE_LIMITER] = RateLimiter(clock=clock, sleep=clock.sleep)
    return clock


@pytest.fixture
def subentries() -> list[ConfigSubentryDataWithId]:
    """Return the test entry's subentries: one area around home."""
    return [
        ConfigSubentryDataWithId(
            subentry_id=AREA_ID,
            subentry_type=SUBENTRY_AREA,
            title="Home",
            unique_id=None,
            data=area_data(),
        )
    ]


@pytest.fixture
def mock_config_entry(subentries: list[ConfigSubentryDataWithId]) -> MockConfigEntry:
    """Return a config entry for the test key."""
    return MockConfigEntry(
        domain=DOMAIN,
        entry_id=ENTRY_ID,
        title="Ontario 511",
        data={CONF_API_KEY: API_KEY},
        unique_id=hashlib.sha256(API_KEY.encode()).hexdigest(),
        version=2,
        subentries_data=subentries,
    )


@pytest.fixture
def mock_api(aioclient_mock: AiohttpClientMocker) -> AiohttpClientMocker:
    """Serve the fixture for every resource."""
    for resource in Resource:
        aioclient_mock.get(resource.url, text=load_fixture(f"{resource.value}.json"))
    return aioclient_mock


@pytest.fixture
async def init_integration(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_api: AiohttpClientMocker,
) -> MockConfigEntry:
    """Set up the integration with the fixtures."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return mock_config_entry


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Stop config flow tests from setting up the entry they create."""
    with patch(
        "custom_components.ontario_511.async_setup_entry", return_value=True
    ) as mock:
        yield mock
