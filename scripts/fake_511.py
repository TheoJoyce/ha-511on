"""A fake Ontario 511 API for local development.

Usage: uv run python -m scripts.fake_511 [--port 8511] [--data .dev-cache/api]

Serves the same paths as the real API from recorded responses, enforces the
real rate limit (10 calls per 60 seconds per key, answering 429 and logging a
warning above it), and rejects a missing key or the key "invalid" like the
real API does. Point the integration at it with
ONTARIO_511_API_BASE=http://127.0.0.1:8511 (scripts/develop does this).

Control endpoints change the data while Home Assistant is running, so every
entity can be seen with data. All take and return JSON:

  GET    /_dev/state                     counts per resource, failures queued
  POST   /_dev/events                    add an event (see _add_event)
  DELETE /_dev/events/{id}               remove an event
  POST   /_dev/conditions                set road conditions (see _set_conditions)
  POST   /_dev/alerts                    add an alert
  POST   /_dev/ferries/{id}              change a ferry terminal's fields
  POST   /_dev/fail                      fail the next N calls of a resource
  POST   /_dev/reset                     reload everything from the data folder
"""

from __future__ import annotations

import argparse
from collections import defaultdict, deque
import json
import logging
from pathlib import Path
import time
from typing import Any

from aiohttp import web

_LOGGER = logging.getLogger("fake_511")
INVALID_KEY = "<Error><Message>Invalid Key</Message></Error>"
RATE_CALLS, RATE_PERIOD = 10, 60.0


class FakeApi:
    """In-memory copy of every resource, plus queued failures."""

    def __init__(self, data_dir: Path) -> None:
        """Load the recorded responses."""
        self.data_dir = data_dir
        self.data: dict[str, list[dict[str, Any]]] = {}
        self.failures: dict[str, deque[int]] = defaultdict(deque)
        self.calls: dict[str, deque[float]] = defaultdict(deque)
        self.next_id = 900_000_000
        self.reset()

    def reset(self) -> None:
        """Reload every resource from the data folder."""
        self.data = {
            path.stem: json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(self.data_dir.glob("*.json"))
        }
        self.failures.clear()

    def new_id(self) -> int:
        """Return an ID no recorded item uses."""
        self.next_id += 1
        return self.next_id

    def over_limit(self, key: str) -> bool:
        """Record a call and return whether the key is over the rate limit."""
        now = time.monotonic()
        window = self.calls[key]
        while window and now - window[0] >= RATE_PERIOD:
            window.popleft()
        window.append(now)
        return len(window) > RATE_CALLS


async def _get_resource(request: web.Request) -> web.Response:
    api: FakeApi = request.app["api"]
    resource = request.match_info["resource"]
    key = request.query.get("key", "")
    if not key or key == "invalid":
        return web.Response(status=400, text=INVALID_KEY, content_type="text/xml")
    if api.over_limit(key):
        _LOGGER.warning(
            "Rate limit exceeded for %s: the integration called too often", resource
        )
        return web.Response(status=429, text="Too many requests")
    if api.failures[resource]:
        status = api.failures[resource].popleft()
        _LOGGER.info("Failing %s with %s as requested", resource, status)
        return web.Response(status=status, text="Simulated failure")
    if resource not in api.data:
        return web.Response(status=404, text="Unknown resource")
    _LOGGER.info("Served %s (%d items)", resource, len(api.data[resource]))
    return web.json_response(api.data[resource])


async def _state(request: web.Request) -> web.Response:
    api: FakeApi = request.app["api"]
    return web.json_response(
        {
            "counts": {name: len(items) for name, items in api.data.items()},
            "failures": {name: list(q) for name, q in api.failures.items() if q},
        }
    )


async def _add_event(request: web.Request) -> web.Response:
    """Add an active event.

    Body: latitude, longitude (required); road, direction, description,
    type ("roadwork", "closures" or "accidentsAndIncidents"), full_closure,
    lanes, hours (how long until its planned end, default 2), resource
    ("event" or "constructionprojects", default "event").
    """
    api: FakeApi = request.app["api"]
    body = await request.json()
    now = int(time.time())
    event = {
        "ID": api.new_id(),
        "SourceId": "dev",
        "Organization": "Dev",
        "RoadwayName": body.get("road", "HWY 401"),
        "DirectionOfTravel": body.get("direction", "Both Directions"),
        "Description": body.get("description", "Injected test event"),
        "Reported": now,
        "LastUpdated": now,
        "StartDate": now - 60,
        "PlannedEndDate": now + int(float(body.get("hours", 2)) * 3600),
        "LanesAffected": body.get("lanes", "ALL LANES CLOSED"),
        "Latitude": float(body["latitude"]),
        "Longitude": float(body["longitude"]),
        "LatitudeSecondary": None,
        "LongitudeSecondary": None,
        "EventType": body.get("type", "accidentsAndIncidents"),
        "EventSubType": None,
        "IsFullClosure": bool(body.get("full_closure", True)),
        "Severity": "Unknown",
        "Comment": None,
        "EncodedPolyline": None,
        "Restrictions": {},
        "Recurrence": "",
        "RecurrenceSchedules": "",
        "LinkId": None,
        "Impact": None,
    }
    api.data.setdefault(body.get("resource", "event"), []).append(event)
    return web.json_response(event, status=201)


async def _remove_event(request: web.Request) -> web.Response:
    api: FakeApi = request.app["api"]
    event_id = int(request.match_info["id"])
    removed = 0
    for name in ("event", "constructionprojects"):
        before = len(api.data.get(name, []))
        api.data[name] = [e for e in api.data.get(name, []) if e.get("ID") != event_id]
        removed += before - len(api.data[name])
    return web.json_response({"removed": removed})


async def _set_conditions(request: web.Request) -> web.Response:
    """Change road conditions.

    Body: condition (for example "Snow covered"), and optionally road (the
    roadway number, such as "401") and match (text in the description) to pick
    segments; visibility ("Good", "Fair", "Poor"); drifting ("Yes" or "No").
    Without road or match, every segment changes.
    """
    api: FakeApi = request.app["api"]
    body = await request.json()
    changed = 0
    for segment in api.data.get("roadconditions", []):
        if "road" in body and str(segment.get("RoadwayName")) != str(body["road"]):
            continue
        if (
            "match" in body
            and body["match"].lower()
            not in (segment.get("LocationDescription") or "").lower()
        ):
            continue
        if "condition" in body:
            segment["Condition"] = [body["condition"]]
        if "visibility" in body:
            segment["Visibility"] = body["visibility"]
        if "drifting" in body:
            segment["Drifting"] = body["drifting"]
        segment["LastUpdated"] = int(time.time())
        changed += 1
    return web.json_response({"changed": changed})


async def _add_alert(request: web.Request) -> web.Response:
    """Add an alert. Body: message, regions (list), high_importance."""
    api: FakeApi = request.app["api"]
    body = await request.json()
    now = int(time.time())
    alert = {
        "Id": api.new_id(),
        "Message": body.get("message", "Injected test alert"),
        "Notes": body.get("notes"),
        "StartTime": now,
        "EndTime": now + 86400,
        "LastUpdated": now,
        "Regions": body.get("regions", ["Central"]),
        "HighImportance": bool(body.get("high_importance", True)),
        "SendNotification": True,
    }
    api.data.setdefault("alerts", []).append(alert)
    return web.json_response(alert, status=201)


async def _change_ferry(request: web.Request) -> web.Response:
    """Change a ferry terminal's fields, for example {"Status": "Not in Service"}."""
    api: FakeApi = request.app["api"]
    body = await request.json()
    ferry_id = int(request.match_info["id"])
    for ferry in api.data.get("ferryterminals", []):
        if ferry.get("Id") == ferry_id:
            ferry.update(body)
            return web.json_response(ferry)
    return web.json_response({"error": "not found"}, status=404)


async def _fail(request: web.Request) -> web.Response:
    """Fail the next calls. Body: resource, status (default 503), count (default 1)."""
    api: FakeApi = request.app["api"]
    body = await request.json()
    for _ in range(int(body.get("count", 1))):
        api.failures[body["resource"]].append(int(body.get("status", 503)))
    return web.json_response({"queued": len(api.failures[body["resource"]])})


async def _reset(request: web.Request) -> web.Response:
    request.app["api"].reset()
    return web.json_response({"reset": True})


def build_app(data_dir: Path) -> web.Application:
    """Create the fake API application."""
    app = web.Application()
    app["api"] = FakeApi(data_dir)
    app.router.add_get(r"/api/v{version:\d}/get/{resource}", _get_resource)
    app.router.add_get("/_dev/state", _state)
    app.router.add_post("/_dev/events", _add_event)
    app.router.add_delete("/_dev/events/{id}", _remove_event)
    app.router.add_post("/_dev/conditions", _set_conditions)
    app.router.add_post("/_dev/alerts", _add_alert)
    app.router.add_post("/_dev/ferries/{id}", _change_ferry)
    app.router.add_post("/_dev/fail", _fail)
    app.router.add_post("/_dev/reset", _reset)
    return app


def main() -> None:
    """Run the fake API."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--port", type=int, default=8511)
    parser.add_argument("--data", type=Path, default=Path(".dev-cache/api"))
    args = parser.parse_args()
    if not any(args.data.glob("*.json")):
        raise SystemExit(
            f"No responses in {args.data}. Record them first: "
            f"uv run python -m scripts.record_fixtures {args.data}"
        )
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    web.run_app(build_app(args.data), host="127.0.0.1", port=args.port, print=None)


if __name__ == "__main__":
    main()
