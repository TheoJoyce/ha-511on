"""Diagnostics for Ontario 511."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant

from . import Ontario511ConfigEntry, subentry_feeds, subentry_items
from .client import DEV_API_KEY, get_rate_limiter
from .const import CONF_PENDING

TO_REDACT = {CONF_API_KEY}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: Ontario511ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry.

    Locations are left out; counts and settings are enough to tell whether
    data is arriving and what was generated.
    """
    limiter = get_rate_limiter(hass)
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "uses_development_key": entry.data[CONF_API_KEY] == DEV_API_KEY,
        },
        "subentries": [
            {
                "type": subentry.subentry_type,
                "feeds": sorted(r.value for r in subentry_feeds(subentry)),
                "pending": list(subentry.data.get(CONF_PENDING, [])),
                "item_counts": {
                    resource.value: len(items)
                    for resource, items in sorted(subentry_items(subentry).items())
                },
            }
            for subentry in entry.subentries.values()
        ],
        "rate_limit": {"calls": limiter.calls, "period_seconds": limiter.period},
        "coordinators": {
            resource.value: {
                "last_update_success": coordinator.last_update_success,
                "update_interval_seconds": (
                    coordinator.update_interval.total_seconds()
                    if coordinator.update_interval
                    else None
                ),
                "item_count": len(coordinator.data or {}),
            }
            for resource, coordinator in sorted(entry.runtime_data.coordinators.items())
        },
    }
