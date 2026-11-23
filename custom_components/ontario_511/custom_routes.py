"""Custom commute routes: pins, and stops read from a pasted route.

Everything here runs locally. A pasted link or GPX file is only read: nothing
is fetched from Google or Apple, short links aren't followed and place names
aren't looked up. The pins reach OpenStreetMap's routing service only when the
user saves the route, as the waypoints of one request.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from itertools import pairwise
import re
from urllib.parse import parse_qs, unquote, urlsplit

from .geo import Point, distance_km
from .models import in_ontario

MAX_PINS = 8
MAX_IMPORT_CHARS = 2_000_000
# Stops this close to the commute's start or end are the start or end.
END_MARGIN_KM = 0.5
# Consecutive stops this close together are the same stop.
SAME_STOP_KM = 0.05

_SHORT_LINK_HOSTS = frozenset({"maps.app.goo.gl", "goo.gl", "g.co", "maps.apple"})
_GOOGLE_HOST = re.compile(r"^(?:www\.|maps\.)?google(?:\.[a-z]{2,3}){1,2}$")
_APPLE_HOST = "maps.apple.com"
_COORDINATES = re.compile(r"^\s*(-?\d{1,2}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\s*$")
_GOOGLE_DATA_POINT = re.compile(r"!1d(-?\d+(?:\.\d+)?)!2d(-?\d+(?:\.\d+)?)")
_GPX_POINT = re.compile(r"<(?:[\w.-]+:)?(trkpt|rtept|wpt)\b([^<>]*)>", re.IGNORECASE)
_GPX_LAT = re.compile(r"""\blat\s*=\s*["'](-?\d+(?:\.\d+)?)["']""")
_GPX_LON = re.compile(r"""\blon\s*=\s*["'](-?\d+(?:\.\d+)?)["']""")
_APPLE_STOP_SEPARATOR = re.compile(r"\+?to:")


class RouteImportError(Exception):
    """A pasted route couldn't be read."""


class UnsupportedRoute(RouteImportError):
    """The text isn't a directions link or GPX file this can read."""


class ShortLink(RouteImportError):
    """A short link, which would have to be fetched to read it."""


class NamedStops(RouteImportError):
    """The link names places instead of giving their coordinates."""


class StopsOutsideOntario(RouteImportError):
    """A stop is outside Ontario."""


class NoStops(RouteImportError):
    """The route has no stops between the commute's start and end."""


def _coordinates(text: str) -> Point | None:
    if (match := _COORDINATES.match(text)) is None:
        return None
    return (float(match[1]), float(match[2]))


def _stop(text: str) -> Point:
    if (point := _coordinates(text)) is None:
        raise NamedStops
    return point


def _google_directions_path(path: str) -> list[Point]:
    """Read /maps/dir/ stops, taking named stops' coordinates from its data."""
    parts = path.split("/maps/dir/", 1)[1].split("/")
    data = next((p for p in parts if p.startswith("data=")), "")
    stops = [
        unquote(p).replace("+", " ")
        for p in parts
        if p and not p.startswith(("@", "data="))
    ]
    named = sum(1 for stop in stops if _coordinates(stop) is None)
    # Longitude comes first in the data.
    located = iter(
        (float(lat), float(lon)) for lon, lat in _GOOGLE_DATA_POINT.findall(data)
    )
    if named and len(_GOOGLE_DATA_POINT.findall(data)) != named:
        raise NamedStops
    return [_coordinates(stop) or next(located) for stop in stops]


def _google(path: str, query: dict[str, list[str]]) -> list[Point]:
    if "/maps/dir/" in path and path.split("/maps/dir/", 1)[1].strip("/"):
        return _google_directions_path(path)
    if "origin" in query or "destination" in query:
        waypoints = [
            stop for value in query.get("waypoints", []) for stop in value.split("|")
        ]
        return [
            _stop(stop)
            for stop in (
                *query.get("origin", []),
                *waypoints,
                *query.get("destination", []),
            )
        ]
    raise UnsupportedRoute


def _apple(query: dict[str, list[str]]) -> list[Point]:
    if "source" in query or "destination" in query:
        stops = [
            *query.get("source", []),
            *query.get("waypoint", []),
            *query.get("destination", []),
        ]
    elif "daddr" in query:
        stops = [
            *query.get("saddr", []),
            *(
                stop
                for value in query["daddr"]
                for stop in _APPLE_STOP_SEPARATOR.split(value)
            ),
        ]
    else:
        raise UnsupportedRoute
    return [_stop(stop) for stop in stops]


def _gpx(text: str) -> list[Point]:
    """Read a GPX file's track, else its route, else its waypoints.

    Matched as text: it's user-supplied, and only two attributes are needed.
    """
    found: dict[str, list[Point]] = {"trkpt": [], "rtept": [], "wpt": []}
    for match in _GPX_POINT.finditer(text):
        lat, lon = _GPX_LAT.search(match[2]), _GPX_LON.search(match[2])
        if lat and lon:
            found[match[1].lower()].append((float(lat[1]), float(lon[1])))
    points = found["trkpt"] or found["rtept"] or found["wpt"]
    if not points:
        raise UnsupportedRoute
    return points


def parse_route(text: str) -> list[Point]:
    """Return a pasted directions link's or GPX file's points, in order."""
    text = text.strip()
    if not text or len(text) > MAX_IMPORT_CHARS:
        raise UnsupportedRoute
    if "<" in text:
        points = _gpx(text)
    else:
        try:
            url = urlsplit(text if "//" in text else f"https://{text}")
            host = (url.hostname or "").lower()
        except ValueError as err:
            raise UnsupportedRoute from err
        query = parse_qs(url.query)
        if host in _SHORT_LINK_HOSTS:
            raise ShortLink
        if _GOOGLE_HOST.match(host):
            points = _google(url.path, query)
        elif host == _APPLE_HOST:
            points = _apple(query)
        else:
            raise UnsupportedRoute
    if not all(in_ontario(lat, lon) for lat, lon in points):
        raise StopsOutsideOntario
    return points


def _spaced(points: Sequence[Point], count: int) -> list[Point]:
    """Return ``count`` points evenly spaced by distance along a line."""
    travelled = [0.0]
    for a, b in pairwise(points):
        travelled.append(travelled[-1] + distance_km(a, b))
    chosen: list[Point] = []
    index = 0
    for slot in range(1, count + 1):
        target = travelled[-1] * slot / (count + 1)
        while index < len(points) - 1 and travelled[index] < target:
            index += 1
        if not chosen or chosen[-1] != points[index]:
            chosen.append(points[index])
    return chosen


def _distinct(points: Sequence[Point]) -> Iterator[Point]:
    last: Point | None = None
    for point in points:
        if last is None or distance_km(last, point) > SAME_STOP_KM:
            yield point
            last = point


def _trip_km(start: Point, stops: Sequence[Point], end: Point) -> float:
    return sum(distance_km(a, b) for a, b in pairwise((start, *stops, end)))


def pins_from_route(
    points: Sequence[Point], start: Point, end: Point, limit: int = MAX_PINS
) -> list[Point]:
    """Return the pins that make a commute follow an imported route.

    The commute's start and end stay its areas' centres. A long track is
    reduced to evenly spaced pins, which is enough to hold the route to it.
    A route recorded the other way round is followed from this start.
    """
    between = [
        point
        for point in _distinct(points)
        if distance_km(point, start) > END_MARGIN_KM
        and distance_km(point, end) > END_MARGIN_KM
    ]
    if not between:
        raise NoStops
    if _trip_km(start, between[::-1], end) < _trip_km(start, between, end):
        between.reverse()
    return between if len(between) <= limit else _spaced(between, limit)


def insert_pin(
    pins: Sequence[Point], pin: Point, start: Point, end: Point
) -> list[Point]:
    """Add a pin where it lengthens the trip least, so pins can go in any order."""
    stops = [start, *pins, end]
    position = min(
        range(1, len(stops)),
        key=lambda i: (
            distance_km(stops[i - 1], pin)
            + distance_km(pin, stops[i])
            - distance_km(stops[i - 1], stops[i])
        ),
    )
    return [*pins[: position - 1], pin, *pins[position - 1 :]]
