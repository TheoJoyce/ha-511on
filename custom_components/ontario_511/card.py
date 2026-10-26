"""Serves the Ontario 511 map card and adds it to the dashboard card picker.

Home Assistant's map card can't draw lines, so the integration ships its own
card for commute routes. It bundles MapLibre GL and uses the same free
OpenStreetMap vector tiles (VersaTiles) as Home Assistant's map.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import DOMAIN

CARD_FILE = "ontario-511-map-card.js"
FRONTEND_DIR = Path(__file__).parent / "frontend"


def frontend_hash() -> str:
    """Return a short hash of the card's files."""
    digest = hashlib.sha256()
    for path in sorted(FRONTEND_DIR.iterdir()):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


async def async_register_card(hass: HomeAssistant) -> None:
    """Serve the card and its files, and load it in the frontend.

    Browsers cache the files for a month, so the URL includes a hash of their
    content: any change to them, in a release or in development, gets a new
    URL instead of a stale cached copy.
    """
    if hass.http is None or "frontend" not in hass.config.components:
        return
    url = f"/{DOMAIN}/{await hass.async_add_executor_job(frontend_hash)}"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(url, str(FRONTEND_DIR), cache_headers=True)]
    )
    add_extra_js_url(hass, f"{url}/{CARD_FILE}")
