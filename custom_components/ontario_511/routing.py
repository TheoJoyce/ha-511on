"""Routing a commute over Ontario 511's road segments.

The road conditions feed covers provincial highways as segments that run
between junctions. They form a network: a segment's end either meets another
segment's end or joins it partway along (a T-junction), so segments are split
where other segments join them. The start and end of a commute connect to
junctions nearby, standing in for local roads, and the shortest routes through
the network are found with Dijkstra's algorithm. Pure functions, so it runs in
the executor and can be tested without Home Assistant.
"""

from __future__ import annotations

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
ACCESS_FACTOR = 1.4
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
    segments, not their full length.
    """

    cost_km: float
    segments: tuple[str, ...]
    pieces: tuple[tuple[str, tuple[Point, ...]], ...]


type _Node = tuple[int, int]
type _Edge = tuple[int, int, int]  # line index, from vertex, to vertex


class RoadNetwork:
    """The road segment network, built once per road conditions update."""

    def __init__(self, segments: Iterable[RoadSegment]) -> None:
        """Build the network from road segments."""
        self._lines: list[tuple[RoadSegment, tuple[Point, ...]]] = [
            (segment, line) for segment in segments for line in segment.lines if line
        ]
        self._parent: dict[_Node, _Node] = {}
        self._cumulative: list[list[float]] = []
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
            for a, b in itertools.pairwise(stops):
                na, nb = self._find((li, a)), self._find((li, b))
                self.nodes.setdefault(na, line[a])
                self.nodes.setdefault(nb, line[b])
                if na == nb:
                    continue
                length = cumulative[b] - cumulative[a]
                self._adjacent.setdefault(na, []).append((nb, length, (li, a, b)))
                self._adjacent.setdefault(nb, []).append((na, length, (li, a, b)))

    def _shortest(
        self, start: Point, end: Point, penalty: dict[int, float]
    ) -> tuple[float, list[_Edge]]:
        near_start = [
            (node, km)
            for node, point in self.nodes.items()
            if (km := distance_km(start, point)) <= ACCESS_KM
        ]
        near_end = {
            node: km
            for node, point in self.nodes.items()
            if (km := distance_km(end, point)) <= ACCESS_KM
        }
        # Driving local roads all the way is always an option.
        best: tuple[float, _Node | None] = (
            distance_km(start, end) * ACCESS_FACTOR,
            None,
        )
        start_node: _Node = (-1, -1)
        distances: dict[_Node, float] = {start_node: 0.0}
        previous: dict[_Node, tuple[_Node, _Edge | None]] = {}
        queue: list[tuple[float, _Node]] = [(0.0, start_node)]
        while queue:
            cost, node = heapq.heappop(queue)
            if cost > distances.get(node, math.inf) or cost >= best[0]:
                continue
            if node in near_end and cost + near_end[node] * ACCESS_FACTOR < best[0]:
                best = (cost + near_end[node] * ACCESS_FACTOR, node)
            edges: list[tuple[_Node, float, _Edge | None]] = (
                [(n, km * ACCESS_FACTOR, None) for n, km in near_start]
                if node == start_node
                else [(n, km, e) for n, km, e in self._adjacent.get(node, [])]
            )
            for nxt, length, edge in edges:
                weighted = length * penalty.get(edge[0], 1.0) if edge else length
                if cost + weighted < distances.get(nxt, math.inf):
                    distances[nxt] = cost + weighted
                    previous[nxt] = (node, edge)
                    heapq.heappush(queue, (cost + weighted, nxt))
        if best[1] is None:
            return best[0], []
        path: list[_Edge] = []
        node = best[1]
        while node != start_node:
            node, edge = previous[node]
            if edge is not None:
                path.append(edge)
        return best[0], path[::-1]

    def _route(self, cost: float, path: list[_Edge]) -> Route:
        segments: list[str] = []
        pieces: list[tuple[str, tuple[Point, ...]]] = []
        for li, a, b in path:
            segment, line = self._lines[li]
            if segment.id not in segments:
                segments.append(segment.id)
            low, high = min(a, b), max(a, b)
            piece = line[low : high + 1]
            pieces.append((segment.id, piece if a <= b else piece[::-1]))
        return Route(cost, tuple(segments), tuple(pieces))

    def routes(self, start: Point, end: Point) -> list[Route]:
        """Return up to three sensible routes from start to end, best first.

        An empty list means local roads all the way beat any highway route.
        """
        found: list[Route] = []
        penalty: dict[int, float] = {}
        for _ in range(MAX_ROUTES + 1):
            cost, path = self._shortest(start, end, penalty)
            if not path:
                break
            route = self._route(cost, path)
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
