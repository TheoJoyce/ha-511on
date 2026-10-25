"""Constants for the Ontario 511 integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

from .api import Resource

DOMAIN: Final = "ontario_511"

CONF_RADIUS: Final = "radius"
DEFAULT_RADIUS_KM: Final = 25.0
MIN_RADIUS_KM: Final = 1.0
MAX_RADIUS_KM: Final = 200.0

# Fixed polling schedule. Together these must stay well under the API limit
# of 10 calls per 60 seconds; POLL_BUDGET_PER_MINUTE caps them and is checked
# by the tests.
UPDATE_INTERVALS: Final[dict[Resource, timedelta]] = {
    Resource.EVENTS: timedelta(minutes=5),
    Resource.ALERTS: timedelta(minutes=5),
}
POLL_BUDGET_PER_MINUTE: Final = 1.0

# Resource used to check a key in the config flow: the smallest response.
VALIDATION_RESOURCE: Final = Resource.ALERTS

# Required attribution under the Open Government Licence - Ontario.
ATTRIBUTION: Final = (
    "Contains information licensed under the Open Government Licence - Ontario."
)
LICENCE_URL: Final = "https://www.ontario.ca/page/open-government-licence-ontario"
SOURCE_URL: Final = "https://511on.ca"
