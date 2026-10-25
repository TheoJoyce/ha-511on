"""Tests that the integration stays within Ontario 511's rate limit.

The API allows 10 calls every 60 seconds and documents no per-endpoint
limits. These tests pin the polling budget, check that every call goes
through the shared limiter, and check that errors don't cause extra calls.
"""

from __future__ import annotations

from datetime import timedelta
import hashlib

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import CONF_API_KEY, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.ontario_511.api import RATE_LIMIT_CALLS, Resource
from custom_components.ontario_511.client import get_rate_limiter
from custom_components.ontario_511.const import (
    DOMAIN,
    POLL_BUDGET_PER_MINUTE,
    UPDATE_INTERVALS,
)

from .conftest import FIXTURE_RESOURCES

POLLED = len(UPDATE_INTERVALS)


def test_polling_budget_is_well_below_limit() -> None:
    """Steady-state polling uses a small fraction of the limit."""
    per_minute = sum(60 / i.total_seconds() for i in UPDATE_INTERVALS.values())
    assert per_minute <= POLL_BUDGET_PER_MINUTE
    assert POLL_BUDGET_PER_MINUTE <= RATE_LIMIT_CALLS / 5


def test_fixtures_cover_polled_resources() -> None:
    """Every polled resource has a recorded fixture."""
    assert set(UPDATE_INTERVALS) == set(FIXTURE_RESOURCES)


async def test_setup_calls_go_through_limiter(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_api: AiohttpClientMocker,
) -> None:
    """Setup makes one call per polled resource, all counted by the limiter."""
    limiter = get_rate_limiter(hass)
    assert mock_api.call_count == POLLED
    assert limiter.available == limiter.calls - POLLED


async def test_limiter_is_shared_across_entries(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_api: AiohttpClientMocker,
) -> None:
    """A second entry with another key draws on the same budget."""
    other_key = "another-key-9876543210"
    second = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_API_KEY: other_key},
        unique_id=hashlib.sha256(other_key.encode()).hexdigest(),
    )
    second.add_to_hass(hass)
    await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()

    limiter = get_rate_limiter(hass)
    assert mock_api.call_count == 2 * POLLED
    assert limiter.available == limiter.calls - 2 * POLLED


async def test_polls_follow_fixed_schedule(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_api: AiohttpClientMocker,
    frozen_time: FrozenDateTimeFactory,
) -> None:
    """No calls happen between scheduled updates."""
    frozen_time.tick(timedelta(minutes=4))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert mock_api.call_count == POLLED

    frozen_time.tick(timedelta(minutes=1, seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert mock_api.call_count == 2 * POLLED


async def test_rate_limited_response_waits_for_next_poll(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_api: AiohttpClientMocker,
    frozen_time: FrozenDateTimeFactory,
) -> None:
    """A 429 marks entities unavailable without retrying early."""
    mock_api.clear_requests()
    for resource in FIXTURE_RESOURCES:
        mock_api.get(resource.url, status=429)

    frozen_time.tick(timedelta(minutes=5, seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert mock_api.call_count == POLLED
    assert hass.states.get("sensor.ontario_511_nearby_events").state == (
        STATE_UNAVAILABLE
    )

    frozen_time.tick(timedelta(minutes=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert mock_api.call_count == POLLED


def test_events_resource_is_polled() -> None:
    """Events and alerts are the resources polled in this release."""
    assert set(UPDATE_INTERVALS) == {Resource.EVENTS, Resource.ALERTS}
