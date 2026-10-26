"""Client for the Ontario 511 REST API.

Kept free of Home Assistant imports so it can be tested on its own and
extracted into a library later.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from enum import StrEnum
import json
from pathlib import Path
import time
from typing import Any, Final

import aiohttp

API_BASE: Final = "https://511on.ca"
REQUEST_TIMEOUT: Final = aiohttp.ClientTimeout(total=30)

# Documented once for the whole API: "Throttling is enabled. Ten calls every
# 60 seconds." No endpoint documents a different limit.
RATE_LIMIT_CALLS: Final = 10
RATE_LIMIT_PERIOD: Final = 60.0
# Absorbs clock and network skew between us and Ontario 511's counter.
RATE_LIMIT_MARGIN: Final = 1.0


class Resource(StrEnum):
    """API resources, valued by their path segment."""

    ALERTS = "alerts"
    CAMERAS = "cameras"
    CARPOOL_LOTS = "carpoollots"
    CONSTRUCTION = "constructionprojects"
    EVENTS = "event"
    FERRY_TERMINALS = "ferryterminals"
    HOV_LANES = "hovlanes"
    INFORMATION_CENTRES = "informationcenter"
    INSPECTION_STATIONS = "inspectionstations"
    REST_AREAS = "allrestareas"
    ROAD_CONDITIONS = "roadconditions"
    ROUNDABOUTS = "roundabouts"
    SEASONAL_LOADS = "seasonalloadapi"
    SERVICE_CENTRES = "servicecentres"
    TRANSIT_HUBS = "transithub"
    TRUCK_REST_AREAS = "truckrestareas"

    @property
    def api_version(self) -> int:
        """Return the API version the resource is published under."""
        return 3 if self is Resource.ROAD_CONDITIONS else 2

    @property
    def url_path(self) -> str:
        """Return the resource's URL path."""
        return f"/api/v{self.api_version}/get/{self.value}"

    @property
    def url(self) -> str:
        """Return the resource URL on the real API, without the key."""
        return f"{API_BASE}{self.url_path}"


class Ontario511Error(Exception):
    """Base error for the Ontario 511 API."""


class Ontario511AuthError(Ontario511Error):
    """The API key was rejected."""


class Ontario511ConnectionError(Ontario511Error):
    """The API could not be reached or returned an unexpected response."""


class Ontario511RateLimitError(Ontario511Error):
    """Ontario 511 reported that the rate limit was exceeded."""


class Ontario511DevCacheError(Ontario511Error):
    """The development key was used without a cached response to read."""


class RateLimiter:
    """Sliding-window limiter: at most ``calls`` acquisitions per ``period``.

    ``acquire`` waits for a free slot instead of failing, so callers can never
    exceed the limit no matter how many of them share the limiter.
    """

    def __init__(
        self,
        calls: int = RATE_LIMIT_CALLS,
        period: float = RATE_LIMIT_PERIOD + RATE_LIMIT_MARGIN,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        """Initialize the limiter."""
        self.calls = calls
        self.period = period
        self._clock = clock
        self._sleep = sleep
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    def _prune(self, now: float) -> None:
        while self._timestamps and now - self._timestamps[0] >= self.period:
            self._timestamps.popleft()

    @property
    def available(self) -> int:
        """Return how many calls can be made right now without waiting."""
        self._prune(self._clock())
        return self.calls - len(self._timestamps)

    async def acquire(self) -> None:
        """Wait until a call is allowed, then record it."""
        async with self._lock:
            while True:
                now = self._clock()
                self._prune(now)
                if len(self._timestamps) < self.calls:
                    self._timestamps.append(now)
                    return
                await self._sleep(self._timestamps[0] + self.period - now)


class Ontario511Client:
    """Fetch resources from the Ontario 511 API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_key: str,
        limiter: RateLimiter,
        language: str = "en",
        base_url: str = API_BASE,
    ) -> None:
        """Initialize the client.

        ``base_url`` is only changed for local development against a fake
        server; it defaults to the real API.
        """
        self._session = session
        self._api_key = api_key
        self._limiter = limiter
        self._language = language
        self._base_url = base_url.rstrip("/")

    async def async_get(self, resource: Resource) -> list[dict[str, Any]]:
        """Return the items of ``resource``.

        Errors never include the request URL, because it carries the key.
        """
        await self._limiter.acquire()
        params = {"key": self._api_key, "format": "json", "lang": self._language}
        try:
            async with self._session.get(
                f"{self._base_url}{resource.url_path}",
                params=params,
                timeout=REQUEST_TIMEOUT,
            ) as response:
                body = await response.text()
                status = response.status
        except (aiohttp.ClientError, TimeoutError) as err:
            raise Ontario511ConnectionError(
                f"Error fetching {resource.value}: {type(err).__name__}"
            ) from None

        if status == 429:
            raise Ontario511RateLimitError(f"Rate limited fetching {resource.value}")
        if status in (400, 401, 403) and "invalid key" in body.lower():
            raise Ontario511AuthError("The API key was rejected")
        if status != 200:
            raise Ontario511ConnectionError(
                f"Unexpected status {status} fetching {resource.value}"
            )
        try:
            data = json.loads(body)
        except ValueError:
            raise Ontario511ConnectionError(
                f"Invalid JSON fetching {resource.value}"
            ) from None
        if not isinstance(data, list):
            raise Ontario511ConnectionError(
                f"Unexpected payload fetching {resource.value}"
            )
        return data


class DevCacheClient(Ontario511Client):
    """Serve resources from saved full responses instead of the API.

    Used with the development key (see client.DEV_API_KEY), so many areas and
    commutes can be tried without spending a real key's calls. It reads files
    only and makes no request, so the rate limiter doesn't apply.
    """

    def __init__(self, cache_dir: Path) -> None:
        """Initialize the client with the directory of saved responses."""
        self._cache_dir = cache_dir

    async def async_get(self, resource: Resource) -> list[dict[str, Any]]:
        """Return the saved items of ``resource``."""
        path = self._cache_dir / f"{resource.value}.json"
        try:
            body = await asyncio.get_running_loop().run_in_executor(
                None, path.read_text
            )
        except OSError:
            raise Ontario511DevCacheError(
                f"No cached response for {resource.value}"
            ) from None
        try:
            data = json.loads(body)
        except ValueError:
            raise Ontario511DevCacheError(
                f"Invalid cached response for {resource.value}"
            ) from None
        if not isinstance(data, list):
            raise Ontario511DevCacheError(
                f"Unexpected cached response for {resource.value}"
            )
        return data
