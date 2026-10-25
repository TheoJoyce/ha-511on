"""Ranking of road conditions.

Ontario 511 reports conditions as free text such as "Bare and dry road" or
"Partly snow covered". Only bare, wet and "No Report" were seen in October
data, so the ranking matches keywords and treats anything unrecognised as
unknown rather than guessing that it's safe.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import IntEnum
import logging

_LOGGER = logging.getLogger(__name__)
_REPORTED_UNKNOWN: set[str] = set()


class RoadCondition(IntEnum):
    """Road conditions from best to worst. ``UNKNOWN`` sorts below ``GOOD``."""

    UNKNOWN = -1
    GOOD = 0
    WET = 1
    PARTLY_COVERED = 2
    COVERED = 3
    ICY = 4
    CLOSED = 5

    @property
    def option(self) -> str:
        """Return the enum sensor option for this condition."""
        return self.name.lower()

    @property
    def is_hazardous(self) -> bool:
        """Return whether the condition is worse than wet."""
        return self >= RoadCondition.PARTLY_COVERED


# Checked in order; the first match wins.
_KEYWORDS: tuple[tuple[tuple[str, ...], RoadCondition], ...] = (
    (("closed",), RoadCondition.CLOSED),
    (("ice", "icy"), RoadCondition.ICY),
    (
        ("partly", "partially", "slush", "patches", "spots"),
        RoadCondition.PARTLY_COVERED,
    ),
    (("snow",), RoadCondition.COVERED),
    (("wet",), RoadCondition.WET),
    (("dry", "bare"), RoadCondition.GOOD),
)


def classify(text: str | None) -> RoadCondition:
    """Return the condition described by one piece of text."""
    if not text or "no report" in (lowered := text.lower()):
        return RoadCondition.UNKNOWN
    for keywords, condition in _KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return condition
    if lowered not in _REPORTED_UNKNOWN:
        _REPORTED_UNKNOWN.add(lowered)
        _LOGGER.info("Unrecognised road condition %r treated as unknown", text)
    return RoadCondition.UNKNOWN


def worst(texts: Iterable[str]) -> RoadCondition:
    """Return the worst condition among several descriptions."""
    return max((classify(text) for text in texts), default=RoadCondition.UNKNOWN)
