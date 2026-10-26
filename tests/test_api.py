"""Tests for the API client and rate limiter."""

from __future__ import annotations

import asyncio
from pathlib import Path

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.common import load_json_array_fixture
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.ontario_511.api import (
    RATE_LIMIT_CALLS,
    RATE_LIMIT_PERIOD,
    DevCacheClient,
    Ontario511AuthError,
    Ontario511Client,
    Ontario511ConnectionError,
    Ontario511DevCacheError,
    Ontario511Error,
    Ontario511RateLimitError,
    RateLimiter,
    Resource,
)

from .conftest import API_KEY


class FakeClock:
    """A monotonic clock that only moves when the limiter sleeps."""

    def __init__(self) -> None:
        """Start at zero."""
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        """Return the current time."""
        return self.now

    async def sleep(self, seconds: float) -> None:
        """Advance time instead of waiting."""
        self.sleeps.append(seconds)
        self.now += seconds


def test_documented_limit() -> None:
    """The defaults match the documented limit, with a safety margin."""
    limiter = RateLimiter()
    assert (RATE_LIMIT_CALLS, RATE_LIMIT_PERIOD) == (10, 60.0)
    assert limiter.calls == 10
    assert limiter.period > 60.0


async def test_limiter_allows_burst_then_waits() -> None:
    """Ten calls go straight through; the eleventh waits for the window."""
    clock = FakeClock()
    limiter = RateLimiter(10, 61.0, clock=clock, sleep=clock.sleep)

    for _ in range(10):
        await limiter.acquire()
    assert clock.sleeps == []
    assert limiter.available == 0

    await limiter.acquire()
    assert clock.sleeps == [61.0]


async def test_limiter_never_exceeds_limit_under_concurrency() -> None:
    """Concurrent callers never get more than the limit in any window."""
    clock = FakeClock()
    limiter = RateLimiter(10, 61.0, clock=clock, sleep=clock.sleep)
    granted: list[float] = []

    async def call() -> None:
        await limiter.acquire()
        granted.append(clock.now)

    await asyncio.gather(*(call() for _ in range(35)))

    assert len(granted) == 35
    for start in granted:
        in_window = [t for t in granted if start <= t < start + 61.0]
        assert len(in_window) <= 10


async def test_limiter_frees_slots_as_window_slides() -> None:
    """Old calls drop out of the window."""
    clock = FakeClock()
    limiter = RateLimiter(10, 61.0, clock=clock, sleep=clock.sleep)
    for _ in range(10):
        await limiter.acquire()
    clock.now = 61.0
    assert limiter.available == 10


async def test_every_request_uses_the_limiter(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Each API call takes one slot from the limiter."""
    aioclient_mock.get(Resource.ALERTS.url, json=[])
    limiter = RateLimiter()
    client = Ontario511Client(async_get_clientsession(hass), API_KEY, limiter)

    await client.async_get(Resource.ALERTS)
    await client.async_get(Resource.ALERTS)

    assert limiter.available == limiter.calls - 2
    assert aioclient_mock.call_count == 2


async def test_request_parameters(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Requests carry the key, JSON format and language."""
    aioclient_mock.get(Resource.ROAD_CONDITIONS.url, json=[])
    client = Ontario511Client(
        async_get_clientsession(hass), API_KEY, RateLimiter(), "fr"
    )

    await client.async_get(Resource.ROAD_CONDITIONS)

    url = aioclient_mock.mock_calls[0][1]
    assert url.path == "/api/v3/get/roadconditions"
    assert dict(url.query) == {"key": API_KEY, "format": "json", "lang": "fr"}


@pytest.mark.parametrize(
    ("mock_kwargs", "error"),
    [
        pytest.param(
            {"status": 400, "text": "<Error><Message>Invalid Key</Message></Error>"},
            Ontario511AuthError,
            id="invalid_key",
        ),
        pytest.param({"status": 429, "text": ""}, Ontario511RateLimitError, id="429"),
        pytest.param(
            {"status": 500, "text": "oops"}, Ontario511ConnectionError, id="500"
        ),
        pytest.param(
            {"status": 200, "text": "not json"},
            Ontario511ConnectionError,
            id="bad_json",
        ),
        pytest.param(
            {"status": 200, "json": {"a": 1}},
            Ontario511ConnectionError,
            id="not_a_list",
        ),
        pytest.param(
            {"exc": aiohttp.ClientConnectionError("boom")},
            Ontario511ConnectionError,
            id="connection",
        ),
        pytest.param({"exc": TimeoutError()}, Ontario511ConnectionError, id="timeout"),
    ],
)
async def test_errors_never_leak_the_key(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_kwargs: dict[str, object],
    error: type[Ontario511Error],
) -> None:
    """Failures map to typed errors whose messages exclude the key."""
    aioclient_mock.get(Resource.EVENTS.url, **mock_kwargs)  # type: ignore[arg-type]
    client = Ontario511Client(async_get_clientsession(hass), API_KEY, RateLimiter())

    with pytest.raises(error) as excinfo:
        await client.async_get(Resource.EVENTS)

    assert API_KEY not in str(excinfo.value)
    assert excinfo.value.__cause__ is None


def test_resource_urls() -> None:
    """Road conditions are the only v3 resource."""
    assert {r for r in Resource if r.api_version == 3} == {Resource.ROAD_CONDITIONS}
    assert Resource.EVENTS.url == "https://511on.ca/api/v2/get/event"


@pytest.mark.parametrize("resource", list(Resource), ids=lambda r: r.value)
def test_every_resource_has_a_fixture(resource: Resource) -> None:
    """Each resource has a fixture with at least the two documented samples."""
    items = load_json_array_fixture(f"{resource.value}.json")
    assert len(items) >= 2


async def test_dev_cache_client(tmp_path: Path) -> None:
    """Saved responses are served as they are; bad or missing ones raise."""
    (tmp_path / "alerts.json").write_text('[{"Id": 1}]')
    (tmp_path / "event.json").write_text('{"not": "a list"}')
    client = DevCacheClient(tmp_path)
    assert await client.async_get(Resource.ALERTS) == [{"Id": 1}]
    with pytest.raises(Ontario511DevCacheError):
        await client.async_get(Resource.EVENTS)
    with pytest.raises(Ontario511DevCacheError):
        await client.async_get(Resource.CAMERAS)
