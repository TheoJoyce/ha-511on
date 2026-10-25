"""Constants for the Ontario 511 integration."""

from __future__ import annotations

from typing import Final

from .api import Resource

DOMAIN: Final = "ontario_511"

# Subentry types.
SUBENTRY_AREA: Final = "area"
SUBENTRY_COMMUTE: Final = "commute"
SUBENTRY_ITEM: Final = "item"

# Subentry data keys.
CONF_LOCATION: Final = "location"
CONF_START: Final = "start"
CONF_END: Final = "end"
CONF_RADIUS_KM: Final = "radius_km"
CONF_CORRIDOR_KM: Final = "corridor_km"
CONF_SEGMENTS: Final = "segments"
CONF_ROUTE: Final = "route"
CONF_FROM_AREA: Final = "from_area"
CONF_TO_AREA: Final = "to_area"
CONF_TYPES: Final = "types"
CONF_FEEDS: Final = "feeds"
CONF_EVENT_SLOTS: Final = "event_slots"
CONF_ITEMS: Final = "items"
CONF_PENDING: Final = "pending"
CONF_RESOURCE: Final = "resource"
CONF_ITEM_ID: Final = "item_id"

DEFAULT_AREA_NAME: Final = "Home"
DEFAULT_DESTINATION_NAME: Final = "Work"
DEFAULT_NEW_AREA_NAME: Final = "Area"
DEFAULT_RADIUS_KM: Final = 25.0
MIN_RADIUS_KM: Final = 0.5
MAX_RADIUS_KM: Final = 50.0
DEFAULT_CORRIDOR_KM: Final = 5.0
MIN_CORRIDOR_KM: Final = 0.5
MAX_CORRIDOR_KM: Final = 20.0
DEFAULT_EVENT_SLOTS: Final = 5

# Resources that come and go; they feed summaries and segments rather than
# becoming devices of their own.
FEED_RESOURCES: Final = (Resource.EVENTS, Resource.CONSTRUCTION, Resource.ALERTS)

# Resources whose items become devices, in the order offered to users.
ITEM_RESOURCES: Final = (
    Resource.ROAD_CONDITIONS,
    Resource.CAMERAS,
    Resource.FERRY_TERMINALS,
    Resource.REST_AREAS,
    Resource.TRUCK_REST_AREAS,
    Resource.SERVICE_CENTRES,
    Resource.SEASONAL_LOADS,
    Resource.CARPOOL_LOTS,
    Resource.TRANSIT_HUBS,
    Resource.INSPECTION_STATIONS,
    Resource.ROUNDABOUTS,
    Resource.HOV_LANES,
    Resource.INFORMATION_CENTRES,
)

# Caps for the initial generation only; Item subentries aren't limited.
DEFAULT_GENERATION_CAP: Final = 25
GENERATION_CAPS: Final[dict[Resource, int]] = {
    Resource.ROAD_CONDITIONS: 40,
    Resource.CAMERAS: 25,
}

# Above this many selected types, the first import takes about a minute
# longer because of the rate limit.
FAST_IMPORT_TYPES: Final = 10

# Resource used to check a key in the config flow: the smallest response.
VALIDATION_RESOURCE: Final = Resource.ALERTS

# Required attribution under the Open Government Licence - Ontario.
ATTRIBUTION: Final = (
    "Contains information licensed under the Open Government Licence - Ontario."
)
LICENCE_URL: Final = "https://www.ontario.ca/page/open-government-licence-ontario"
SOURCE_URL: Final = "https://511on.ca"
