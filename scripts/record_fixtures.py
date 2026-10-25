"""Record live Ontario 511 responses as JSON for test fixtures.

Usage: uv run python -m scripts.record_fixtures OUT_DIR [RESOURCE ...]

Reads ONTARIO_511_API_KEY from .env. Every request goes through the same
RateLimiter the integration uses, so recording all 16 resources takes just
over a minute. Do not run this while a development Home Assistant instance is
using the same key: the two would not share a limiter.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

import aiohttp

from custom_components.ontario_511.api import Ontario511Client, RateLimiter, Resource


def _read_key() -> str:
    for line in Path(".env").read_text(encoding="utf-8").splitlines():
        name, _, value = line.partition("=")
        if name.strip() == "ONTARIO_511_API_KEY" and value.strip():
            return value.strip()
    raise SystemExit("ONTARIO_511_API_KEY is not set in .env")


async def _record(out_dir: Path, resources: list[Resource]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    limiter = RateLimiter()
    async with aiohttp.ClientSession() as session:
        client = Ontario511Client(session, _read_key(), limiter)
        for resource in resources:
            items = await client.async_get(resource)
            path = out_dir / f"{resource.value}.json"
            path.write_text(json.dumps(items, indent=2) + "\n", encoding="utf-8")
            print(f"{resource.value}: {len(items)} items -> {path}")


def main() -> None:
    """Run the recorder."""
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    resources = [Resource(name) for name in sys.argv[2:]] or list(Resource)
    asyncio.run(_record(Path(sys.argv[1]), resources))


if __name__ == "__main__":
    main()
