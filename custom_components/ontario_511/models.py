"""Data models for Ontario 511 resources."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Self

# Rough bounding box of Ontario. The feed occasionally publishes coordinates
# with a flipped sign; those items are kept but get no location.
_LAT_RANGE = (41.0, 57.5)
_LON_RANGE = (-95.5, -74.0)


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, int | float) and not isinstance(value, bool) and value > 0:
        return datetime.fromtimestamp(value, UTC)
    return None


def _text(value: Any) -> str | None:
    if isinstance(value, str) and (stripped := value.strip()):
        return stripped
    return None


def _coordinates(item: dict[str, Any]) -> tuple[float, float] | None:
    lat, lon = item.get("Latitude"), item.get("Longitude")
    if not isinstance(lat, int | float) or not isinstance(lon, int | float):
        return None
    if not (
        _LAT_RANGE[0] <= lat <= _LAT_RANGE[1] and _LON_RANGE[0] <= lon <= _LON_RANGE[1]
    ):
        return None
    return float(lat), float(lon)


@dataclass(frozen=True, slots=True)
class Event:
    """A traffic event: roadwork, a closure or an incident."""

    id: str
    road: str | None
    direction: str | None
    description: str | None
    event_type: str | None
    event_subtype: str | None
    lanes_affected: str | None
    is_full_closure: bool
    coordinates: tuple[float, float] | None
    start: datetime | None
    planned_end: datetime | None
    last_updated: datetime | None
    comment: str | None

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> Self:
        """Build an event from an API item."""
        return cls(
            id=str(item["ID"]),
            road=_text(item.get("RoadwayName")),
            direction=_text(item.get("DirectionOfTravel")),
            description=_text(item.get("Description")),
            event_type=_text(item.get("EventType")),
            event_subtype=_text(item.get("EventSubType")),
            lanes_affected=_text(item.get("LanesAffected")),
            is_full_closure=item.get("IsFullClosure") is True,
            coordinates=_coordinates(item),
            start=_timestamp(item.get("StartDate")),
            planned_end=_timestamp(item.get("PlannedEndDate")),
            last_updated=_timestamp(item.get("LastUpdated")),
            comment=_text(item.get("Comment")),
        )

    def is_active(self, now: datetime) -> bool:
        """Return whether the event has started and not passed its planned end."""
        if self.start is not None and self.start > now:
            return False
        return self.planned_end is None or self.planned_end >= now


@dataclass(frozen=True, slots=True)
class Alert:
    """A province-wide traveller alert."""

    id: str
    message: str | None
    notes: str | None
    regions: tuple[str, ...]
    high_importance: bool
    start: datetime | None
    end: datetime | None

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> Self:
        """Build an alert from an API item."""
        regions = item.get("Regions")
        return cls(
            id=str(item["Id"]),
            message=_text(item.get("Message")),
            notes=_text(item.get("Notes")),
            regions=tuple(str(r) for r in regions) if isinstance(regions, list) else (),
            high_importance=item.get("HighImportance") is True,
            start=_timestamp(item.get("StartTime")),
            end=_timestamp(item.get("EndTime")),
        )
