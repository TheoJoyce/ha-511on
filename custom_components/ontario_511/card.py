"""Serves the Ontario 511 map card and adds it to the dashboard card picker.

Home Assistant's map card can't draw lines, so the integration ships its own
card for commute routes. It bundles MapLibre GL and uses the same free
OpenStreetMap vector tiles (VersaTiles) as Home Assistant's map.
"""

from __future__ import annotations

from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN

CARD_FILE = "ontario-511-map-card.js"
FRONTEND_DIR = Path(__file__).parent / "frontend"


async def async_register_card(hass: HomeAssistant) -> None:
    """Serve the card and its files, and load it in the frontend.

    The URL includes the integration version, so browsers can cache the files
    for good and still pick up new ones after an update.
    """
    if hass.http is None or "frontend" not in hass.config.components:
        return
    integration = await async_get_integration(hass, DOMAIN)
    url = f"/{DOMAIN}/{integration.version}"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(url, str(FRONTEND_DIR), cache_headers=True)]
    )
    add_extra_js_url(hass, f"{url}/{CARD_FILE}")
