"""Constants for the Ontario 511 integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "ontario_511"

API_BASE_URL: Final = "https://511on.ca/api/v2/get"

# Ontario 511 throttles each developer key to 10 calls every 60 seconds.
RATE_LIMIT_CALLS: Final = 10
RATE_LIMIT_PERIOD_SECONDS: Final = 60

# Required attribution under the Open Government Licence - Ontario.
ATTRIBUTION: Final = (
    "Contains information licensed under the Open Government Licence - Ontario."
)
LICENCE_URL: Final = "https://www.ontario.ca/page/open-government-licence-ontario"
SOURCE_URL: Final = "https://511on.ca"
