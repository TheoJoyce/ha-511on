"""Tests that the integration stays within Ontario 511's rate limit.

The API allows 10 calls every 60 seconds and documents no per-endpoint
limits. These tests pin the polling budget, check that every call goes
through the shared limiter, check that forced refreshes reuse fresh data,
and check that errors don't cause extra calls.
"""

from __future__ import annotations

from datetime import timedelta
import hashlib

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.ontario_511 import resources_in_use
from custom_components.ontario_511.api import RATE_LIMIT_CALLS, Resource
from custom_components.ontario_511.client import get_rate_limiter
from custom_components.ontario_511.const import DOMAIN, SUBENTRY_AREA
from custom_components.ontario_511.coordinator import ResourceCoordinator
from custom_components.ontario_511.policy import (
    POLL_BUDGET_PER_MINUTE,
    RESOURCE_POLICIES,
    Category,
)

from .conftest import area_data

# The test entry's area follows events and alerts; events need road segments.
POLLED = 3


@pytest.fixture
def listening(init_integration: MockConfigEntry) -> MockConfigEntry:
    """Listen to every coordinator the way entities do, so they poll."""
    for coordinator in init_integration.runtime_data.coordinators.values():
        init_integration.async_on_unload(coordinator.async_add_listener(lambda: None))
    return init_integration


def _calls_for(mock: AiohttpClientMocker, resource: Resource) -> int:
    return sum(1 for call in mock.mock_calls if call[1].path == resource.url_path)


def test_polling_budget_is_well_below_limit() -> None:
    """Background polling of every resource uses a tenth of the limit or less."""
    per_minute = sum(p.calls_per_minute for p in RESOURCE_POLICIES.values())
    assert per_minute <= POLL_BUDGET_PER_MINUTE
    assert POLL_BUDGET_PER_MINUTE <= RATE_LIMIT_CALLS / 10


def test_every_resource_has_a_policy() -> None:
    """Each resource has a policy whose freshness is shorter than its refresh."""
    assert set(RESOURCE_POLICIES) == set(Resource)
    for policy in RESOURCE_POLICIES.values():
        assert policy.freshness < policy.refresh


@pytest.mark.parametrize("category", list(Category))
def test_live_data_refreshes_fastest(category: Category) -> None:
    """Live resources refresh at least as often as periodic, then static ones."""
    order = [Category.LIVE, Category.PERIODIC, Category.STATIC]
    slowest_here = max(
        p.refresh for p in RESOURCE_POLICIES.values() if p.category is category
    )
    for later in order[order.index(category) + 1 :]:
        fastest_later = min(
            p.refresh for p in RESOURCE_POLICIES.values() if p.category is later
        )
        assert slowest_here <= fastest_later


async def test_setup_calls_go_through_limiter(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_api: AiohttpClientMocker,
) -> None:
    """Setup makes one call per resource in use, all counted by the limiter."""
    assert resources_in_use(init_integration) == {
        Resource.EVENTS,
        Resource.ALERTS,
        Resource.ROAD_CONDITIONS,
    }
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
        version=2,
        subentries_data=[
            ConfigSubentryData(
                subentry_type=SUBENTRY_AREA,
                title="Cottage",
                unique_id=None,
                data=area_data(),
            )
        ],
    )
    second.add_to_hass(hass)
    await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()

    limiter = get_rate_limiter(hass)
    assert mock_api.call_count == 2 * POLLED
    assert limiter.available == limiter.calls - 2 * POLLED


@pytest.mark.usefixtures("listening")
async def test_polls_follow_each_resource_policy(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_api: AiohttpClientMocker,
    frozen_time: FrozenDateTimeFactory,
) -> None:
    """Each resource is polled on its own interval and not in between."""
    events_every = RESOURCE_POLICIES[Resource.EVENTS].refresh
    alerts_every = RESOURCE_POLICIES[Resource.ALERTS].refresh
    assert events_every < alerts_every

    frozen_time.tick(events_every - timedelta(seconds=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert mock_api.call_count == POLLED

    frozen_time.tick(timedelta(seconds=10))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _calls_for(mock_api, Resource.EVENTS) == 2
    assert _calls_for(mock_api, Resource.ALERTS) == 1

    frozen_time.tick(alerts_every - events_every)
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _calls_for(mock_api, Resource.ALERTS) == 2


def _coordinator(entry: MockConfigEntry, resource: Resource) -> ResourceCoordinator:
    return entry.runtime_data.coordinators[resource]


async def test_forced_refresh_reuses_fresh_data(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_api: AiohttpClientMocker,
    frozen_time: FrozenDateTimeFactory,
) -> None:
    """A forced refresh only calls the API once the cached data is stale.

    "Update entity" on any entity calls the coordinator's request refresh.
    """
    coordinator = _coordinator(init_integration, Resource.EVENTS)
    freshness = coordinator.policy.freshness

    await coordinator.async_request_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _calls_for(mock_api, Resource.EVENTS) == 1

    frozen_time.tick(freshness + timedelta(seconds=1))
    await coordinator.async_request_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _calls_for(mock_api, Resource.EVENTS) == 2

    await coordinator.async_request_refresh()
    await coordinator.async_request_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _calls_for(mock_api, Resource.EVENTS) == 2


@pytest.mark.usefixtures("listening")
async def test_rate_limited_response_waits_for_next_poll(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_api: AiohttpClientMocker,
    frozen_time: FrozenDateTimeFactory,
) -> None:
    """A 429 marks entities unavailable without retrying early."""
    mock_api.clear_requests()
    for resource in Resource:
        mock_api.get(resource.url, status=429)
    events_every = RESOURCE_POLICIES[Resource.EVENTS].refresh

    frozen_time.tick(events_every + timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _calls_for(mock_api, Resource.EVENTS) == 1
    assert not _coordinator(init_integration, Resource.EVENTS).last_update_success

    frozen_time.tick(events_every - timedelta(seconds=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _calls_for(mock_api, Resource.EVENTS) == 1


@pytest.mark.usefixtures("listening")
async def test_forced_refresh_after_failure_calls_again(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_api: AiohttpClientMocker,
    frozen_time: FrozenDateTimeFactory,
) -> None:
    """Data from before a failed update isn't treated as fresh."""
    mock_api.clear_requests()
    for resource in Resource:
        mock_api.get(resource.url, status=503)
    coordinator = _coordinator(init_integration, Resource.EVENTS)
    frozen_time.tick(coordinator.policy.refresh)
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _calls_for(mock_api, Resource.EVENTS) == 1

    frozen_time.tick(timedelta(seconds=30))
    await coordinator.async_request_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _calls_for(mock_api, Resource.EVENTS) == 2


async def test_reload_reuses_cached_responses(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_api: AiohttpClientMocker,
) -> None:
    """Reloading the entry (after adding an item) doesn't re-fetch anything."""
    assert mock_api.call_count == POLLED
    assert await hass.config_entries.async_reload(init_integration.entry_id)
    await hass.async_block_till_done()
    assert mock_api.call_count == POLLED
