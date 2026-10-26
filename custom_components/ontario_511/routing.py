"""Routing a commute over Ontario 511's road segments.

The road conditions feed covers provincial highways as segments that run
between junctions. They form a network: a segment's end either meets another
segment's end or joins it partway along (a T-junction), so segments are split
where other segments join them. The start and end of a commute connect to
points of the network nearby by local roads (straight lines, weighted as
slower), and the shortest routes through the network are found with
Dijkstra's algorithm. Pure functions, so it runs in
the executor and can be tested without Home Assistant.
"""

from __future__ import annotations

import bisect
from collections.abc import Iterable
from dataclasses import dataclass
import heapq
import itertools
import math

from .geo import Point, distance_km
from .models import RoadSegment

# Ends within this distance of another segment are treated as joined.
JUNCTION_KM = 1.5
# Junctions this close to a commute's start or end can begin or finish it.
ACCESS_KM = 30.0
# Local roads to and from the network are slower and less direct.
ACCESS_FACTOR = 2.0
# A highway route must cost at least this much less than local roads.
MIN_SAVING = 0.05
# Alternatives must be within this factor of the best route's cost.
ALTERNATIVE_FACTOR = 1.3
MAX_ROUTES = 3
# Used segments cost this much more when looking for alternatives.
ALTERNATIVE_PENALTY = 1.5
_GRID = 0.02  # Degrees; about 2 km, enough to find junctions nearby.


@dataclass(frozen=True, slots=True)
class Route:
    """A route through the road network.

    ``segments`` are the IDs of segments used, in travel order. ``pieces`` are
    the stretches actually driven, each with its segment's ID: parts of
    segments, not their full length. ``entry`` and ``exit`` are where the
    route joins and leaves the network; local roads connect them to the start
    and end.
    """

    cost_km: float
    segments: tuple[str, ...]
    pieces: tuple[tuple[str, tuple[Point, ...]], ...]
    entry: Point
    exit: Point


type _Node = tuple[int, int]
type _Edge = tuple[int, int, int]  # line index, from vertex, to vertex
type _Access = list[tuple[int, int, float]]  # line index, vertex, km away


class RoadNetwork:
    """The road segment network, built once per road conditions update.

    Routes can join and leave a segment at any of its points, as drivers can
    at any interchange, not only where segments meet.
    """

    def __init__(self, segments: Iterable[RoadSegment]) -> None:
        """Build the network from road segments."""
        self._lines: list[tuple[RoadSegment, tuple[Point, ...]]] = [
            (segment, line) for segment in segments for line in segment.lines if line
        ]
        self._parent: dict[_Node, _Node] = {}
        self._cumulative: list[list[float]] = []
        self._stops: list[list[int]] = []
        self.nodes: dict[_Node, Point] = {}
        self._adjacent: dict[_Node, list[tuple[_Node, float, _Edge]]] = {}
        self._build()

    def _find(self, node: _Node) -> _Node:
        self._parent.setdefault(node, node)
        while self._parent[node] != node:
            self._parent[node] = self._parent[self._parent[node]]
            node = self._parent[node]
        return node

    def _build(self) -> None:
        grid: dict[tuple[int, int], list[tuple[int, int, Point]]] = {}
        for li, (_, line) in enumerate(self._lines):
            for vi, point in enumerate(line):
                grid.setdefault(_cell(point), []).append((li, vi, point))

        junctions = {li: {0, len(line) - 1} for li, (_, line) in enumerate(self._lines)}
        for li, (_, line) in enumerate(self._lines):
            for vi in (0, len(line) - 1):
                point = line[vi]
                cx, cy = _cell(point)
                nearest: dict[int, tuple[float, int]] = {}
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        for lj, vj, other in grid.get((cx + dx, cy + dy), []):
                            if lj == li:
                                continue
                            km = distance_km(point, other)
                            known = nearest.get(lj, (math.inf, 0))[0]
                            if km <= JUNCTION_KM and km < known:
                                nearest[lj] = (km, vj)
                for lj, (_, vj) in nearest.items():
                    junctions[lj].add(vj)
                    self._parent[self._find((li, vi))] = self._find((lj, vj))

        for li, (_, line) in enumerate(self._lines):
            cumulative = [0.0]
            for here, there in itertools.pairwise(line):
                cumulative.append(cumulative[-1] + distance_km(here, there))
            self._cumulative.append(cumulative)
            stops = sorted(junctions[li])
            self._stops.append(stops)
            for a, b in itertools.pairwise(stops):
                na, nb = self._find((li, a)), self._find((li, b))
                self.nodes.setdefault(na, line[a])
                self.nodes.setdefault(nb, line[b])
                if na == nb:
                    continue
                length = cumulative[b] - cumulative[a]
                self._adjacent.setdefault(na, []).append((nb, length, (li, a, b)))
                self._adjacent.setdefault(nb, []).append((na, length, (li, a, b)))

    def _access(self, point: Point) -> _Access:
        """Return the points of the network close enough to reach by local roads."""
        # Degrees of latitude and longitude that ACCESS_KM can't exceed here.
        dlat = ACCESS_KM / 110.0
        dlon = ACCESS_KM / (110.0 * max(0.1, math.cos(math.radians(point[0]))))
        return [
            (li, vi, km)
            for li, (_, line) in enumerate(self._lines)
            for vi, other in enumerate(line)
            if abs(other[0] - point[0]) <= dlat
            and abs(other[1] - point[1]) <= dlon
            and (km := distance_km(point, other)) <= ACCESS_KM
        ]

    def _interval(self, li: int, vi: int) -> tuple[int, int]:
        """Return the junctions on either side of a point of a line."""
        stops = self._stops[li]
        i = min(bisect.bisect_right(stops, vi) - 1, len(stops) - 2)
        return stops[i], stops[i + 1]

    def _along(self, li: int, a: int, b: int) -> float:
        cumulative = self._cumulative[li]
        return abs(cumulative[b] - cumulative[a])

    def _shortest(
        self,
        start: Point,
        end: Point,
        near_start: _Access,
        near_end: _Access,
        penalty: dict[int, float],
    ) -> tuple[float, list[_Edge]]:
        """Return the cheapest path, or no path when local roads are cheaper.

        The path's first edge starts where the route joins the network and its
        last edge ends where it leaves; either may have no length.
        """

        def weight(li: int) -> float:
            return penalty.get(li, 1.0)

        # Driving local roads all the way is always an option, and a highway
        # route has to save a little to be worth following.
        local = distance_km(start, end) * ACCESS_FACTOR * (1 - MIN_SAVING)
        best_cost: float = local
        best_node: _Node | None = None
        best_tail: list[_Edge] = []

        # Joining and leaving within one stretch between junctions.
        by_interval: dict[tuple[int, int], list[tuple[int, float]]] = {}
        for li, vi, km in near_end:
            by_interval.setdefault((li, self._interval(li, vi)[0]), []).append((vi, km))
        for li, vi, km in near_start:
            for vj, km_end in by_interval.get((li, self._interval(li, vi)[0]), []):
                cost = (km + km_end) * ACCESS_FACTOR + self._along(li, vi, vj) * weight(
                    li
                )
                if cost < best_cost:
                    best_cost, best_node, best_tail = cost, None, [(li, vi, vj)]

        exits: dict[_Node, list[tuple[float, _Edge]]] = {}
        for li, vi, km in near_end:
            for stop in self._interval(li, vi):
                extra = km * ACCESS_FACTOR + self._along(li, stop, vi) * weight(li)
                exits.setdefault(self._find((li, stop)), []).append(
                    (extra, (li, stop, vi))
                )

        start_node: _Node = (-1, -1)
        distances: dict[_Node, float] = {start_node: 0.0}
        previous: dict[_Node, tuple[_Node, _Edge]] = {}
        queue: list[tuple[float, _Node]] = [(0.0, start_node)]
        while queue:
            cost, node = heapq.heappop(queue)
            if cost > distances.get(node, math.inf) or cost >= best_cost:
                continue
            for extra, edge in exits.get(node, []):
                if cost + extra < best_cost:
                    best_cost, best_node, best_tail = cost + extra, node, [edge]
            if node == start_node:
                edges = [
                    (
                        self._find((li, stop)),
                        km * ACCESS_FACTOR + self._along(li, vi, stop) * weight(li),
                        (li, vi, stop),
                    )
                    for li, vi, km in near_start
                    for stop in self._interval(li, vi)
                ]
            else:
                edges = [
                    (n, km * weight(e[0]), e)
                    for n, km, e in self._adjacent.get(node, [])
                ]
            for nxt, length, edge in edges:
                if cost + length < distances.get(nxt, math.inf):
                    distances[nxt] = cost + length
                    previous[nxt] = (node, edge)
                    heapq.heappush(queue, (cost + length, nxt))
        if not best_tail:
            return local, []
        path: list[_Edge] = []
        node = best_node if best_node is not None else start_node
        while node != start_node:
            node, edge = previous[node]
            path.append(edge)
        return best_cost, [*path[::-1], *best_tail]

    def _route(self, cost: float, path: list[_Edge]) -> Route:
        segments: list[str] = []
        pieces: list[tuple[str, tuple[Point, ...]]] = []
        for li, a, b in path:
            if a == b:
                continue
            segment, line = self._lines[li]
            if segment.id not in segments:
                segments.append(segment.id)
            low, high = min(a, b), max(a, b)
            piece = line[low : high + 1]
            pieces.append((segment.id, piece if a <= b else piece[::-1]))
        first, last = path[0], path[-1]
        return Route(
            cost,
            tuple(segments),
            tuple(pieces),
            self._lines[first[0]][1][first[1]],
            self._lines[last[0]][1][last[2]],
        )

    def routes(self, start: Point, end: Point) -> list[Route]:
        """Return up to three sensible routes from start to end, best first.

        An empty list means local roads all the way are as good as any
        highway route.
        """
        near_start, near_end = self._access(start), self._access(end)
        found: list[Route] = []
        penalty: dict[int, float] = {}
        for _ in range(MAX_ROUTES + 1):
            cost, path = self._shortest(start, end, near_start, near_end, penalty)
            if not path:
                break
            route = self._route(cost, path)
            if not route.pieces:
                break
            if found and cost > found[0].cost_km * ALTERNATIVE_FACTOR:
                break
            if all(set(route.segments) != set(r.segments) for r in found):
                found.append(route)
            if len(found) >= MAX_ROUTES:
                break
            for li, _, _ in path:
                penalty[li] = penalty.get(li, 1.0) * ALTERNATIVE_PENALTY
        return found


def _cell(point: Point) -> tuple[int, int]:
    return (int(point[0] // _GRID), int(point[1] // _GRID))
