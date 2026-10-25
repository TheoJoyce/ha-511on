"""Data models for Ontario 511 resources.

Every model has an ``id`` (derived from stable fields where the API has no
``Id``), a display ``name`` and ``coordinates`` (None when missing or outside
Ontario). Parsing never trusts types: the feed has nulls, strings for numbers
and stray whitespace.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import math
from typing import Any, Self

from .api import Resource
from .conditions import RoadCondition, worst
from .geo import BoundingBox, Point, decode_polyline

# Rough bounding box of Ontario. The feed occasionally publishes coordinates
# with a flipped sign; those items are kept but get no location.
_LAT_RANGE = (41.0, 57.5)
_LON_RANGE = (-95.5, -74.0)


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, int | float) and not isinstance(value, bool) and value > 0:
        return datetime.fromtimestamp(value, UTC)
    return None


def _text(value: Any) -> str | None:
    if isinstance(value, str) and (collapsed := " ".join(value.split())):
        return collapsed
    return None


def _int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _flag(value: Any) -> bool | None:
    """Parse the feed's yes/no values ("Y", "Yes", "N", "No")."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and (lowered := value.strip().lower()):
        if lowered in ("y", "yes", "true"):
            return True
        if lowered in ("n", "no", "false"):
            return False
    return None


def _in_ontario(lat: Any, lon: Any) -> Point | None:
    if (
        isinstance(lat, int | float)
        and isinstance(lon, int | float)
        and _LAT_RANGE[0] <= lat <= _LAT_RANGE[1]
        and _LON_RANGE[0] <= lon <= _LON_RANGE[1]
    ):
        return float(lat), float(lon)
    return None


def _coordinates(item: dict[str, Any]) -> Point | None:
    return _in_ontario(item.get("Latitude"), item.get("Longitude"))


def _derived_id(*parts: Any) -> str:
    """Return a stable ID for items the API publishes without one."""
    key = "|".join("" if part is None else str(part).strip().lower() for part in parts)
    return hashlib.sha1(key.encode(), usedforsecurity=False).hexdigest()[:16]


def _api_id(item: dict[str, Any], name: str = "Id") -> str:
    value = item[name]
    if value is None:
        raise KeyError(name)
    return str(value)


# Polylines are thinned to roughly one point per this distance. Matching items
# to roads within a kilometre or two doesn't need more, and it keeps memory low
# (the road conditions feed holds about 240,000 points).
LINE_SPACING_KM = 0.25


def _thin(points: list[Point]) -> tuple[Point, ...]:
    # A flat approximation is plenty for spacing points a few hundred metres
    # apart, and much cheaper than a great-circle distance per point.
    lon_scale = math.cos(math.radians(points[0][0]))
    limit_sq = (LINE_SPACING_KM / 111.2) ** 2
    kept = [points[0]]
    last_lat, last_lon = points[0]
    for lat, lon in points[1:-1]:
        dlat, dlon = lat - last_lat, (lon - last_lon) * lon_scale
        if dlat * dlat + dlon * dlon >= limit_sq:
            kept.append((lat, lon))
            last_lat, last_lon = lat, lon
    if len(points) > 1:
        kept.append(points[-1])
    return tuple(kept)


def _lines(value: Any) -> tuple[tuple[Point, ...], ...]:
    encoded = [value] if isinstance(value, str) else value
    if not isinstance(encoded, list):
        return ()
    lines = []
    for text in encoded:
        if isinstance(text, str) and text:
            try:
                points = [p for p in decode_polyline(text) if _in_ontario(*p)]
            except ValueError:
                continue
            if points:
                lines.append(_thin(points))
    return tuple(lines)


@dataclass(frozen=True, slots=True, kw_only=True)
class Item:
    """Fields shared by every resource."""

    id: str
    name: str
    coordinates: Point | None
    region: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class TrafficEvent(Item):
    """An event or construction project: roadwork, a closure or an incident."""

    road: str | None
    direction: str | None
    description: str | None
    event_type: str | None
    event_subtype: str | None
    severity: str | None
    lanes_affected: str | None
    is_full_closure: bool
    organization: str | None
    comment: str | None
    impact: str | None
    recurrence: str | None
    restrictions: dict[str, Any]
    start: datetime | None
    planned_end: datetime | None
    reported: datetime | None
    last_updated: datetime | None

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> Self:
        """Build an event from an API item."""
        restrictions = item.get("Restrictions")
        road = _text(item.get("RoadwayName"))
        direction = _text(item.get("DirectionOfTravel"))
        return cls(
            id=_api_id(item, "ID"),
            name=" ".join(p for p in (road, direction) if p) or f"Event {item['ID']}",
            coordinates=_coordinates(item),
            road=road,
            direction=direction,
            description=_text(item.get("Description")),
            event_type=_text(item.get("EventType")),
            event_subtype=_text(item.get("EventSubType")),
            severity=_text(item.get("Severity")),
            lanes_affected=_text(item.get("LanesAffected")),
            is_full_closure=item.get("IsFullClosure") is True,
            organization=_text(item.get("Organization")),
            comment=_text(item.get("Comment")),
            impact=_text(item.get("Impact")),
            recurrence=_text(item.get("Recurrence")),
            restrictions={
                key: value
                for key, value in (
                    restrictions.items() if isinstance(restrictions, dict) else ()
                )
                if value is not None
            },
            start=_timestamp(item.get("StartDate")),
            planned_end=_timestamp(item.get("PlannedEndDate")),
            reported=_timestamp(item.get("Reported")),
            last_updated=_timestamp(item.get("LastUpdated")),
        )

    def is_active(self, now: datetime) -> bool:
        """Return whether the event has started and not passed its planned end."""
        if self.start is not None and self.start > now:
            return False
        return self.planned_end is None or self.planned_end >= now


@dataclass(frozen=True, slots=True, kw_only=True)
class Alert(Item):
    """A traveller alert for one or more regions."""

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
        message = _text(item.get("Message"))
        return cls(
            id=_api_id(item),
            name=message or f"Alert {item['Id']}",
            coordinates=None,
            message=message,
            notes=_text(item.get("Notes")),
            regions=tuple(text for r in regions if (text := _text(r)) is not None)
            if isinstance(regions, list)
            else (),
            high_importance=item.get("HighImportance") is True,
            start=_timestamp(item.get("StartTime")),
            end=_timestamp(item.get("EndTime")),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class RoadSegment(Item):
    """A stretch of highway with a reported road condition."""

    road: str | None
    description: str | None
    conditions: tuple[str, ...]
    condition: RoadCondition
    visibility: str | None
    drifting: bool | None
    last_updated: datetime | None
    lines: tuple[tuple[Point, ...], ...]
    bbox: BoundingBox | None = field(compare=False)

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> Self:
        """Build a road segment from an API item."""
        road = _text(item.get("RoadwayName"))
        description = _text(item.get("LocationDescription"))
        raw = item.get("Condition")
        conditions = tuple(
            text
            for value in (raw if isinstance(raw, list) else [raw])
            if (text := _text(value)) is not None
        )
        lines = _lines(item.get("EncodedPolyline"))
        points = [p for line in lines for p in line]
        label = f"Hwy {road}" if road and road.isdigit() else road
        return cls(
            id=_derived_id(road, description),
            name=": ".join(p for p in (label, description) if p) or "Road segment",
            coordinates=_midpoint(lines),
            region=_text(item.get("Region")),
            road=road,
            description=description,
            conditions=conditions,
            condition=worst(conditions),
            visibility=_text(item.get("Visibility")),
            drifting=_flag(item.get("Drifting")),
            last_updated=_timestamp(item.get("LastUpdated")),
            lines=lines,
            bbox=BoundingBox(points) if points else None,
        )

    @property
    def is_hazardous(self) -> bool:
        """Return whether conditions, visibility or drifting make driving risky."""
        return (
            self.condition.is_hazardous
            or self.drifting is True
            or (self.visibility or "").lower() == "poor"
        )


def _midpoint(lines: tuple[tuple[Point, ...], ...]) -> Point | None:
    """Return the vertex halfway along the longest line, for the map."""
    if not lines:
        return None
    longest = max(lines, key=len)
    return longest[len(longest) // 2]


@dataclass(frozen=True, slots=True)
class CameraView:
    """One view of a camera."""

    id: str
    url: str
    enabled: bool
    description: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class Camera(Item):
    """A traffic camera with one or more views."""

    road: str | None
    direction: str | None
    source: str | None
    views: tuple[CameraView, ...]

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> Self:
        """Build a camera from an API item."""
        views = item.get("Views")
        return cls(
            id=_api_id(item),
            name=_text(item.get("Location")) or f"Camera {item['Id']}",
            coordinates=_coordinates(item),
            road=_text(item.get("Roadway")),
            direction=_text(item.get("Direction")),
            source=_text(item.get("Source")),
            views=tuple(
                CameraView(
                    id=str(view["Id"]),
                    url=view["Url"],
                    enabled=str(view.get("Status", "")).lower() == "enabled",
                    description=_text(view.get("Description")),
                )
                for view in (views if isinstance(views, list) else [])
                if isinstance(view, dict)
                and view.get("Id") is not None
                and isinstance(view.get("Url"), str)
            ),
        )


class FerryStatus:
    """Documented ferry statuses, as enum sensor options."""

    OPTIONS = ("in_service", "in_service_off_schedule", "not_in_service")

    @staticmethod
    def parse(value: Any) -> str | None:
        """Map the feed's status text (in any casing) to an option.

        "No Status Available", or anything else, is None, which Home
        Assistant shows as its standard Unknown state.
        """
        text = (_text(value) or "").lower()
        return {
            "in service": "in_service",
            "in service off schedule": "in_service_off_schedule",
            "not in service": "not_in_service",
        }.get(text)


@dataclass(frozen=True, slots=True, kw_only=True)
class FerryTerminal(Item):
    """A ferry terminal and its service status."""

    status: str | None
    status_text: str | None
    details: str | None
    operated_by: str | None
    website: str | None
    out_of_service: datetime | None
    return_to_service: datetime | None

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> Self:
        """Build a ferry terminal from an API item."""
        return cls(
            id=_api_id(item),
            name=_text(item.get("LocationDescription")) or f"Ferry {item['Id']}",
            coordinates=_coordinates(item),
            region=_text(item.get("Region")),
            status=FerryStatus.parse(item.get("Status")),
            status_text=_text(item.get("Status")),
            details=_text(item.get("Details")),
            operated_by=_text(item.get("OperatedBy")),
            website=_text(item.get("Website")),
            out_of_service=_timestamp(item.get("EstimatedOutOfServiceTime")),
            return_to_service=_timestamp(item.get("EstimatedReturnToServiceTime")),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class RestArea(Item):
    """A rest area, truck rest area, picnic park or lay-by."""

    road: str | None
    direction: str | None
    location: str | None
    area_type: str | None
    is_open: bool | None
    status_text: str | None
    season: str | None
    truck_parking: bool | None
    lavatory: bool | None
    accessible: bool | None
    food_services: str | None
    fuel: bool | None
    comments: str | None

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> Self:
        """Build a rest area from an API item."""
        status = _text(item.get("Status"))
        lowered = (status or "").lower()
        return cls(
            id=_derived_id(
                item.get("Name"), item.get("Roadway"), item.get("Direction")
            ),
            name=_text(item.get("Name")) or "Rest area",
            coordinates=_coordinates(item),
            region=_text(item.get("Region")),
            road=_text(item.get("Roadway")),
            direction=_text(item.get("Direction")),
            location=_text(item.get("Location")),
            area_type=_text(item.get("Type")),
            is_open=True
            if lowered == "open"
            else False
            if lowered == "closed"
            else None,
            status_text=status,
            season=_text(item.get("Open")),
            truck_parking=_flag(item.get("TruckParking")),
            lavatory=_flag(item.get("Lavatory")),
            accessible=_flag(item.get("Accessible")),
            food_services=_text(item.get("FoodServices")),
            fuel=_flag(item.get("Fuel")),
            comments=_text(item.get("Comments")),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ServiceCentre(Item):
    """A highway service centre."""

    road: str | None
    access_via: str | None
    food_services: str | None
    amenities: str | None
    fuel: str | None
    commercial_parking: int | None
    website: str | None

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> Self:
        """Build a service centre from an API item."""
        return cls(
            id=_api_id(item),
            name=_text(item.get("Name")) or f"Service centre {item['Id']}",
            coordinates=_coordinates(item),
            region=_text(item.get("Region")),
            road=_text(item.get("Roadway")),
            access_via=_text(item.get("AccessVia")),
            food_services=_text(item.get("FoodServices")),
            amenities=_text(item.get("Amenities")),
            fuel=_text(item.get("FuelProvider")),
            commercial_parking=_int(item.get("CommercialParking")),
            website=_text(item.get("Website")),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class SeasonalLoad(Item):
    """A highway segment with seasonal load restrictions."""

    route: str | None
    status: str | None
    restricted: bool | None
    date: str | None

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> Self:
        """Build a seasonal load segment from an API item."""
        status = _text(item.get("Status"))
        lowered = (status or "").lower()
        return cls(
            # The same name and route can appear twice, so include the location.
            id=_derived_id(
                item.get("SegmentName"),
                item.get("RouteDescription"),
                item.get("Latitude"),
                item.get("Longitude"),
            ),
            name=_text(item.get("SegmentName")) or "Seasonal load segment",
            coordinates=_coordinates(item),
            route=_text(item.get("RouteDescription")),
            status=status,
            restricted=(
                False
                if lowered.startswith("no restriction")
                else True
                if "restriction" in lowered
                else None
            ),
            date=_text(item.get("Date")),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class CarpoolLot(Item):
    """A carpool lot."""

    highway: str | None
    interchange: str | None
    enter_from: str | None
    parking_spaces: int | None
    accessible_spaces: int | None
    bike_rack: bool | None
    lights: bool | None
    transit: bool | None

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> Self:
        """Build a carpool lot from an API item."""
        highway = _text(item.get("Highway"))
        location = _text(item.get("LocationDescription"))
        return cls(
            id=_api_id(item),
            name=" at ".join(p for p in (highway, location) if p) or "Carpool lot",
            coordinates=_coordinates(item),
            region=_text(item.get("Region")),
            highway=highway,
            interchange=_text(item.get("Interchange")),
            enter_from=_text(item.get("EnterLotFrom")),
            parking_spaces=_int(item.get("ParkingSpaces")),
            accessible_spaces=_int(item.get("AccessibleSpaces")),
            bike_rack=_flag(item.get("Bike")),
            lights=_flag(item.get("Lights")),
            transit=_flag(item.get("Transit")),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class TransitHub(Item):
    """A transit hub such as a GO station."""

    address: str | None
    website: str | None

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> Self:
        """Build a transit hub from an API item."""
        return cls(
            id=_api_id(item),
            name=_text(item.get("Name")) or f"Transit hub {item['Id']}",
            coordinates=_coordinates(item),
            region=_text(item.get("Region")),
            address=_text(item.get("Address")),
            website=_text(item.get("Website")),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class InspectionStation(Item):
    """A commercial vehicle inspection station."""

    highway: str | None
    direction: str | None
    location: str | None
    contact: str | None

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> Self:
        """Build an inspection station from an API item."""
        return cls(
            id=_api_id(item),
            name=_text(item.get("Name")) or f"Inspection station {item['Id']}",
            coordinates=_coordinates(item),
            region=_text(item.get("Region")),
            highway=_text(item.get("Highway")),
            direction=_text(item.get("Direction")),
            location=_text(item.get("Location")),
            contact=_text(item.get("ContactInformation")),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Roundabout(Item):
    """A roundabout on a provincial highway."""

    roundabout_type: str | None
    location: str | None

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> Self:
        """Build a roundabout from an API item."""
        return cls(
            id=_derived_id(item.get("Name"), item.get("Location")),
            name=_text(item.get("Name")) or "Roundabout",
            coordinates=_coordinates(item),
            region=_text(item.get("Region")),
            roundabout_type=_text(item.get("Type")),
            location=_text(item.get("Location")),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class HovLane(Item):
    """A high-occupancy (HOT/HOV) lane."""

    road: str | None
    lines: tuple[tuple[Point, ...], ...]

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> Self:
        """Build an HOV lane from an API item."""
        road = _text(item.get("Roadway"))
        lane = _text(item.get("Name"))
        return cls(
            id=_api_id(item),
            name=" ".join(p for p in (road, lane) if p) or f"HOV lane {item['Id']}",
            coordinates=_coordinates(item),
            region=_text(item.get("Region")),
            road=road,
            lines=_lines(item.get("EncodedPolyline")),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class InformationCentre(Item):
    """A travel information centre."""

    hours: str | None
    address: str | None
    phone: str | None
    email: str | None

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> Self:
        """Build a travel information centre from an API item."""
        return cls(
            id=_api_id(item),
            name=_text(item.get("Name")) or f"Information centre {item['Id']}",
            coordinates=_coordinates(item),
            region=_text(item.get("Region")),
            hours=_text(item.get("Open")),
            address=_text(item.get("Address")),
            phone=_text(item.get("PhoneNumber")),
            email=_text(item.get("Email")),
        )


PARSERS: dict[Resource, Callable[[dict[str, Any]], Item]] = {
    Resource.ALERTS: Alert.from_api,
    Resource.CAMERAS: Camera.from_api,
    Resource.CARPOOL_LOTS: CarpoolLot.from_api,
    Resource.CONSTRUCTION: TrafficEvent.from_api,
    Resource.EVENTS: TrafficEvent.from_api,
    Resource.FERRY_TERMINALS: FerryTerminal.from_api,
    Resource.HOV_LANES: HovLane.from_api,
    Resource.INFORMATION_CENTRES: InformationCentre.from_api,
    Resource.INSPECTION_STATIONS: InspectionStation.from_api,
    Resource.REST_AREAS: RestArea.from_api,
    Resource.ROAD_CONDITIONS: RoadSegment.from_api,
    Resource.ROUNDABOUTS: Roundabout.from_api,
    Resource.SEASONAL_LOADS: SeasonalLoad.from_api,
    Resource.SERVICE_CENTRES: ServiceCentre.from_api,
    Resource.TRANSIT_HUBS: TransitHub.from_api,
    Resource.TRUCK_REST_AREAS: RestArea.from_api,
}
