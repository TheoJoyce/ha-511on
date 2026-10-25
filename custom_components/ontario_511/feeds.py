"""Where events, construction and alerts belong.

Events and construction come and go, so they're never entities of their own.
They're attached to the road segment they're on and counted towards each area
or commute they fall in. The work is done once per data update and shared by
every entity that needs it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigSubentry
from homeassistant.util import dt as dt_util

from .api import Resource
from .const import (
    CONF_CORRIDOR_KM,
    CONF_LOCATION,
    CONF_RADIUS_KM,
    CONF_SEGMENTS,
    SUBENTRY_COMMUTE,
)
from .geo import Point
from .models import Alert, Item, RoadSegment, TrafficEvent
from .scope import (
    attach_to_segments,
    segments_within_radius,
    within_corridor,
    within_radius,
)

if TYPE_CHECKING:
    from . import Ontario511ConfigEntry


def normalize_region(region: str) -> str:
    """Make region names comparable ("Northwest" and "Northwestern" match)."""
    lowered = region.strip().lower()
    return lowered.removesuffix("ern") if lowered.endswith("ern") else lowered


@dataclass(slots=True)
class Scope:
    """What falls inside one area or commute."""

    centre: Point | None
    # Active events and construction, nearest (area) or in route order
    # (commute) first, each with its distance in kilometres.
    events: list[tuple[TrafficEvent, float]] = field(default_factory=list)
    construction: list[tuple[TrafficEvent, float]] = field(default_factory=list)
    segments: list[RoadSegment] = field(default_factory=list)
    regions: set[str] = field(default_factory=set)
    alerts: list[Alert] = field(default_factory=list)


class FeedIndex:
    """Attachments and scopes, rebuilt when the underlying data changes."""

    def __init__(self, entry: Ontario511ConfigEntry) -> None:
        """Initialize the index for an entry."""
        self._entry = entry
        # The data objects the index was built from. Holding them (rather than
        # their ids) means a rebuild can't be skipped by an id being reused.
        self._sources: tuple[dict[str, Item] | None, ...] | None = None
        self._by_segment: dict[str, tuple[list[TrafficEvent], list[TrafficEvent]]] = {}
        self._scopes: dict[str, Scope] = {}

    def _source(self, resource: Resource) -> dict[str, Item] | None:
        coordinator = self._entry.runtime_data.coordinators.get(resource)
        return coordinator.data if coordinator else None

    def _data(self, resource: Resource) -> dict[str, Item]:
        return self._source(resource) or {}

    def _rebuild_if_needed(self) -> None:
        sources = (
            Resource.EVENTS,
            Resource.CONSTRUCTION,
            Resource.ROAD_CONDITIONS,
            Resource.ALERTS,
        )
        current = tuple(self._source(r) for r in sources)
        if self._sources is not None and all(
            a is b for a, b in zip(current, self._sources, strict=True)
        ):
            return
        self._sources = current
        now = dt_util.utcnow()
        events = [
            e
            for e in self._data(Resource.EVENTS).values()
            if isinstance(e, TrafficEvent) and e.is_active(now)
        ]
        construction = [
            e
            for e in self._data(Resource.CONSTRUCTION).values()
            if isinstance(e, TrafficEvent) and e.is_active(now)
        ]
        segments = [
            s
            for s in self._data(Resource.ROAD_CONDITIONS).values()
            if isinstance(s, RoadSegment)
        ]
        alerts = [
            a for a in self._data(Resource.ALERTS).values() if isinstance(a, Alert)
        ]

        events_by = attach_to_segments(events, segments)
        construction_by = attach_to_segments(construction, segments)
        self._by_segment = {
            seg_id: (events_by.get(seg_id, []), construction_by.get(seg_id, []))
            for seg_id in events_by.keys() | construction_by.keys()
        }
        by_id = {s.id: s for s in segments}
        self._scopes = {
            subentry.subentry_id: self._build_scope(
                subentry, events, construction, segments, by_id, alerts
            )
            for subentry in self._entry.subentries.values()
            if subentry.subentry_type != "item"
        }

    @staticmethod
    def _build_scope(
        subentry: ConfigSubentry,
        events: list[TrafficEvent],
        construction: list[TrafficEvent],
        segments: list[RoadSegment],
        by_id: dict[str, RoadSegment],
        alerts: list[Alert],
    ) -> Scope:
        if subentry.subentry_type == SUBENTRY_COMMUTE:
            route = [by_id[i] for i in subentry.data[CONF_SEGMENTS] if i in by_id]
            width = subentry.data[CONF_CORRIDOR_KM]
            scope = Scope(
                centre=None,
                events=within_corridor(events, route, width),
                construction=within_corridor(construction, route, width),
                segments=route,
            )
        else:
            location = subentry.data[CONF_LOCATION]
            centre = (location["latitude"], location["longitude"])
            radius = subentry.data[CONF_RADIUS_KM]
            scope = Scope(
                centre=centre,
                events=within_radius(events, centre, radius),
                construction=within_radius(construction, centre, radius),
                segments=[
                    s for s, _ in segments_within_radius(segments, centre, radius)
                ],
            )
        scope.regions = {normalize_region(s.region) for s in scope.segments if s.region}
        scope.alerts = [
            a
            for a in alerts
            if not a.regions or scope.regions & {normalize_region(r) for r in a.regions}
        ]
        return scope

    def on_segment(
        self, segment_id: str
    ) -> tuple[list[TrafficEvent], list[TrafficEvent]]:
        """Return the active events and construction attached to a segment."""
        self._rebuild_if_needed()
        return self._by_segment.get(segment_id, ([], []))

    def scope(self, subentry_id: str) -> Scope:
        """Return what falls inside an area or commute."""
        self._rebuild_if_needed()
        return self._scopes.get(subentry_id, Scope(centre=None))
