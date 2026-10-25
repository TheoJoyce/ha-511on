"""Diagnostics for Ontario 511."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant

from . import Ontario511ConfigEntry
from .client import get_rate_limiter

TO_REDACT = {CONF_API_KEY}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: Ontario511ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry.

    Item details and the home location are left out; counts are enough to
    tell whether data is arriving.
    """
    data = entry.runtime_data
    limiter = get_rate_limiter(hass)
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "rate_limit": {
            "calls": limiter.calls,
            "period_seconds": limiter.period,
        },
        "coordinators": {
            coordinator.resource.value: {
                "last_update_success": coordinator.last_update_success,
                "update_interval_seconds": (
                    coordinator.update_interval.total_seconds()
                    if coordinator.update_interval
                    else None
                ),
                "item_count": len(coordinator.data or {}),
            }
            for coordinator in (data.events, data.alerts)
        },
    }
