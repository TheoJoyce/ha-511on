# Design

How the integration models Ontario 511 data in Home Assistant. This is the reference for what gets generated, how it's organised, and how fresh each kind of data is kept. For where every API field ends up, see [`api-mapping.md`](api-mapping.md). Rules in `AGENTS.md` (rate limits, compliance) still apply.

## Principles

- **Entities map to API data.** Every enabled data type produces entities a user can automate on. Nothing is hidden behind a summary only.
- **Only what's nearby, commute first.** Nothing is generated for the whole province. Items along a commute come first, then items nearest to an area's centre.
- **No temporary entities.** Things that come and go (events, construction) are attached to stable entities: the road segment, area or commute they fall in.
- **Everything works from the UI.** Setup, adding items, and forcing a refresh never need YAML.
- **API cost is per data type, not per item.** One call returns a whole resource for the province, so the number of generated entities doesn't change how many calls are made.

## Structure

One **config entry** per API key. It holds three kinds of **config subentries**, which Home Assistant shows grouped on the integration page, each with its own "Add" button:

| Subentry | What the user sets | What it generates |
|---|---|---|
| **Area** | Name, centre and radius (drag a pin and circle on the map, or type the radius in metres; up to 50 km, and 0 is fine for an area that's only a commute's start or end) | Items of the chosen data types, nearest to the centre first, and a summary device |
| **Commute** | The two areas it runs between (an existing one, or a new area added in the same flow), corridor width (default 5 km, max 20 km). Routes along the roads between the areas' centres are worked out automatically (see [Routing](#routing)); the main route's segments can be adjusted, or the routes planned again, by reconfiguring | Items of the chosen data types along any of its routes, and a summary device covering both areas and the main route (door to door) |
| **Item** | A data type, then one item from a list sorted by distance | That one item. No cap applies |

Setup chains: API key, then a first area ("Home", 25 km), then optionally a commute, which first adds the area it goes to ("Work").

Areas, commutes and items can name a **Home Assistant area** (`ha_area`). It's passed as each new device's `suggested_area`, which Home Assistant only applies when a device is first created, so users can still move devices individually. Choosing a different one when reconfiguring moves all of that subentry's devices. An area can also **create a matching zone** once, at creation, through the zone integration's storage collection; the zone isn't linked to the area afterwards.

Areas carry a link key as their subentry unique ID; commutes reference their two areas by it. If a linked area is deleted, the commute keeps its corridor.

### One-time data type selection

Creating an area or commute shows a checkbox list of data types. It is used once, to decide what to generate, and is **not saved**; only the generated items are stored. Items can be added or removed afterwards without it.

If more than 10 data types are selected, the form warns that the rest load about a minute later: the first 10 calls go out immediately and the rate limiter spaces the rest.

### Generation order and caps

1. Items within a commute's corridor, ordered along the route.
2. Items nearest to the area's centre.

Each data type has a cap for this initial generation so a busy area doesn't flood Home Assistant: all road segments in range, the 25 nearest cameras, and up to 25 of each other type. Commute items fill the cap first, so a long commute uses the cap before nearby but off-route items. The caps don't limit what can be added later with **Item**.

Measured with live data (October 2026), a 50 km radius holds at most 27 road segments, 107 events and 19 construction projects (Toronto). Cameras are the only type that gets large (357 around Toronto); every other type stays at or under 34.

## Devices and entities

Home Assistant defines a device as "a physical device … or a service". Each generated item is a **service device**, and each area and commute has a **summary device**. Devices belong to the subentry that generated them, so the integration page shows them under that area or commute. Service is the right type: the integration reads a remote data service and controls nothing, and Core's Trafikverket integrations (Sweden's equivalent of Ontario 511: cameras, weather stations, ferries) use it too. The frontend draws the same icon for every service device row on the integration page, and there's no per-device icon to change that.

**Each piece of data is its own entity**, following Home Assistant's guidance that attributes only further explain the state and shouldn't hold static information. This follows the Trafikverket camera's pattern: a camera entity plus one sensor per field on the same device. Attributes are kept for what explains a state (the feed's own text in `reported`, a view's description and ID, the capped list of events behind a count) and the location the map needs. Static attributes such as locations are excluded from the recorder.

**Full data, statuses rather than yes/no.** The point of the integration is full data access for automations, so:

- **Every field is exposed.** Only the identifiers and names that make up a device, and polylines, aren't entities. `api-mapping.md` lists every field.
- **Values with more than two states are status (enum) sensors**, with translated states and Unknown (`None`) for anything unrecognised, keeping the raw text in `reported`. Examples: Road condition, Visibility, a rest area's and a seasonal load's Status, a ferry's Status.
- **Combined statuses keep their parts.** Road condition is the overall condition, including drifting snow; Surface condition, Drifting snow and Reported conditions are still separate entities.
- **Granular and duplicate entities are disabled by default** (the parts of a combined status, yes/no versions of a status, region, website and identifiers), so devices aren't cluttered but every value is one click away.
- **Events and alerts carry every field** wherever they appear: `items` lists, Nearest event attributes and the data of New event and New alert.

| Device | Entities (in brackets: disabled by default) |
|---|---|
| Road segment | Road condition (status, includes drifting snow), Visibility (status), Road, Hazardous, Events (count; list in attributes), Construction (count; list in attributes), Full closure (binary), New event (event entity), Last updated (diagnostic) [Surface condition, Reported conditions, Drifting snow, Region] |
| Camera | One camera entity per view, Road, Direction [Source, Source ID, Sort order] |
| Ferry terminal | Status, Details, Estimated return to service, Operated by [Estimated out of service, Website, Twitter, Region] |
| Rest area, truck rest area | Status, Truck parking, Washrooms, Accessible, Fuel, Type, Road, Direction, Location, Season, Food services, Comments [Open, Mode, Region] |
| Service centre | Fuel (the field can include times fuel isn't available), Food services, Amenities, Access, Road, Commercial parking spaces [Website, Title, Region] |
| Seasonal load | Status, Route, Date [Load restrictions] |
| Carpool lot | Parking spaces, Accessible parking spaces, Road, Interchange, Entrance, Bike rack, Lighting, Transit connection [Region] |
| Transit hub, inspection station, roundabout, HOV lane, travel info centre | Every field the feed has, one entity each [Region, Website] |
| Area or commute summary | Worst road condition, Hazardous conditions (binary), Full closure (binary), Events, Construction, New event, Nearest event 1 to 3, Alerts for their region and New alert; Route for commutes |

- **Event entities** fire when something new appears (a new event on a segment, a new alert). They are stable entities whose state is the time of the last event.
- **Attaching events to segments:** each event or construction project is assigned to the nearest road segment within 2 km of it. Live data puts 95% of events within 150 m of a segment. Anything further away counts only towards the area or commute summary.
- **Region alerts:** alerts list the regions they affect. An area's or commute's regions come from the road segments in it; alerts listing no region count everywhere.

### Map

The map card shows any entity with numeric `latitude` and `longitude` attributes, and the built-in Map dashboard adds them automatically. Each item's main entity carries its location, so items appear on the map and move if the data moves.

### Routing

Road conditions cover provincial highways only (not, for example, Toronto's Gardiner Expressway or any local road), so they can't route a commute door to door on their own. Routes come from OSRM on the FOSSGIS server (`directions.py`, see [OpenStreetMap services](#openstreetmap-services)), which routes over every road:

1. One request asks for the fastest route and the service's own alternatives. The service often offers none, so:
2. The road segment network (below) suggests highway routes. For each one the routes found so far don't already use, the route through a point halfway along it is requested, up to three routes in all (at most three requests).
3. Routes more than 30% slower than the fastest, and routes that are 90% the same as one already kept, are dropped.
4. Each route is matched to the road segments it runs along: points every 200 m are matched to the nearest segment within 300 m (Ontario 511's polylines are approximate), and runs shorter than 1 km (crossings, ramps) don't count. The pieces are stored as polylines, simplified to within 10 m, each with its segment ID or none.

The fastest route is the commute's **main route**: its pieces are the corridor, and its segments are the summary's. Items are generated along all of them. Toronto to Barrie, for example, follows the streets to Highway 400 and up it to Barrie, matching its three segments; no other route is within 30%.

**Fallback.** When routing is switched off or fails, the form says so and offers to try again or to use the road segment network alone (`routing.py`). Segments form a network, split where other segments join them partway. A route can join and leave a segment at any of its points (about every 250 m), as drivers can at any interchange; local roads to and from it are straight lines weighted at twice their length, as a stand-in for being slower and less direct. Dijkstra's algorithm finds the cheapest route, then up to two alternatives within 30% of it. A highway route has to cost at least 5% less than local roads all the way, or the commute follows the straight line. These commutes have `routing: highways`, and can be planned again along roads from their settings.

**Commutes from before 0.1.0-alpha.2** were planned over highways only and have no `routing` in their data. A fixable repair asks to plan each one again; it isn't done in the background, because planning sends the start and end to OpenStreetMap. Planning again uses the areas' current centres and generates the commute's data types again, which adds items along the new routes; existing items stay.

### Map card

**Commute routes** need lines, which Home Assistant's map card can't draw (it only draws an entity's own location history). Each commute's **Route** sensor holds the main route as a `route` attribute, the other routes as `alternatives`, and, for commutes planned over highways only, the straight stand-ins for local roads as `local` (all excluded from the recorder), with the main route's length as its state. The card draws alternatives faded under the main route, and `local` dashed. The integration serves its own card, `custom:ontario-511-map-card` (`frontend/`), from a static path named after a hash of its files (browsers cache them for a month, so changed files need a new URL) and registers it with `add_extra_js_url`. It draws routes as lines, other located entities as markers, and zones. It bundles MapLibre GL 6.14.0 (BSD-3-Clause, licence alongside) and uses VersaTiles' OpenStreetMap vector tiles, as Home Assistant 2026.10's own map does; it doesn't depend on the frontend's internal map element, which changed engines in 2026.

**Nearest events** put events on the map without temporary entities. Each area and commute has three fixed entities ("Nearest event 1" to "Nearest event 3"). Entity N shows the Nth nearest active event: its location, description and details. When there are fewer than three events, the rest are Unknown. Home Assistant's dynamic alternative, `geo_location` entities, can't belong to a device or subentry, and automations can only match them by source and zone, so they couldn't be tied to an area or commute.

## Data policy

Each resource has one policy, defined in one place (`const.RESOURCE_POLICIES`) and used everywhere: polling, caching and the tests.

- **Refresh interval:** how often the data is fetched in the background.
- **Freshness:** how long a fetched response counts as current. Inside this window, a forced refresh reuses the cached response instead of calling the API.
- **Category:** live, periodic or static. It says how much a change matters and guides the two numbers above.

| Resource | Category | Refresh interval | Freshness | Why |
|---|---|---|---|---|
| Events | Live | 3 min | 2 min | New incidents and closures matter quickly |
| Alerts | Live | 5 min | 2 min | Rare, but important when they appear |
| Road conditions | Live | 10 min | 5 min | Updated in batches (most segments within the same hour) |
| Ferry terminals | Live | 10 min | 5 min | Service status changes during the day |
| Construction | Periodic | 30 min | 15 min | Changes over days (median 50 hours since last update) |
| Rest areas, truck rest areas | Periodic | 1 h | 30 min | Open or closed status, mostly seasonal |
| Service centres | Periodic | 1 h | 30 min | Fuel field can include outage times |
| Seasonal loads | Periodic | 6 h | 1 h | Restrictions change seasonally |
| Cameras (list) | Static | 24 h | 6 h | Camera locations rarely change; images are separate |
| Carpool lots, transit hubs, inspection stations, roundabouts, HOV lanes, travel info centres | Static | 24 h | 6 h | Reference data |

With every resource enabled, background refreshes come to about 0.82 calls per minute, under the 1 call per minute budget in `tests/test_rate_limits.py` (the API allows 10).

How these were chosen (October 2026): a 12-minute test fetching events, road conditions, alerts, ferries and construction every 2 minutes saw no changes, but it ran around midnight on a Saturday, so it shows a lower bound only. `LastUpdated` fields from a full recording showed road conditions updated in hourly batches, a few events changing per hour, and construction unchanged for days. The intervals are to be re-checked with daytime and winter data.

### Caching and forced refresh

- Every API response is cached whole, per resource. All entities of that type read from the cache.
- Background refreshes follow the refresh interval.
- A forced refresh uses Home Assistant's **Update entity** action, available in the automation editor for any entity. If the cached response is within its freshness window, nothing is fetched and the entities simply re-read the cache. Otherwise one call refreshes the whole resource, so refreshing camera A also refreshes every other camera.
- Forced refreshes are debounced and go through the rate limiter like every other call.
- There is no way to bypass the freshness window. Freshness windows are kept at or below how often the source data changes, so a newer response would rarely differ.

### Camera images

Camera images come from Ontario 511's web map (`511on.ca/map/Cctv/<id>`), not from the rate-limited API. Home Assistant dashboards re-request a camera image about every 10 seconds while it's on screen. Images are fetched only when requested and cached per view.

Measured October 2026: the image server sends `Cache-Control: max-age=20`, and images changed about every 1 to 2 minutes (some cameras less often). Each view's image is cached for 60 seconds, so a dashboard showing a camera fetches it at most once a minute and the image is rarely more than a minute behind.

## OpenStreetMap services

Areas can be placed by typing an address, looked up with OpenStreetMap Nominatim (`geocode.py`), and commutes are routed along roads with OSRM on the FOSSGIS server (`directions.py`). Their usage policies ([Nominatim](https://operations.osmfoundation.org/policies/nominatim/), [FOSSGIS routing](https://routing.openstreetmap.de/about.html)) set the rules, which `osm.py` and the two modules follow:

- Only a user submitting a form (or a repair) triggers a request; no autocomplete and nothing periodic or in the background. Limits apply to the integration as a whole, across every install.
- At most one request per second per service and Home Assistant instance; identical requests are answered from a cache.
- Requests send the integration's own User-Agent (`ha-511on/<version> (+repository URL)`).
- Either service can be switched off without a software update: before a request, `geocoding.json` on the repository's `main` branch is checked (cached for an hour). Address search needs `"enabled": true` there and routing `"routing": true`; otherwise, or if it can't be read, requests are refused. Without routing, commutes fall back to the highway network.
- The forms show OpenStreetMap attribution and say the address, or the commute's start and end, are sent to OpenStreetMap.
- Address results outside Ontario are rejected.

## Data quirks

- 27% of road segments report "No Report"; that is treated as unknown, not as a good condition.
- Ferry status values don't match the documented casing ("Not In Service" vs "Not in Service"); statuses are compared case-insensitively.
- Some seasonal load statuses have trailing spaces.
- Road segments, rest areas, roundabouts and seasonal loads have no `Id` field. Their IDs are derived from road and description (road segments), name, road and direction (rest areas), name and location (roundabouts), or segment name, route and coordinates (seasonal loads, where one name and route pair is duplicated).
- Some events are published with a flipped longitude sign; coordinates outside Ontario are treated as missing.
- October data only shows bare, wet and "No Report" road conditions. The condition ranking must handle values it hasn't seen, and is to be checked against winter data.

## Later

- Custom commute routes: pins placed along the way, sent to the same routing as waypoints.
- Importing a route from Google Maps, Apple Maps or similar, likely as waypoints for the same routing.
- Linking areas to commutes.
- Checking the road condition ranking against winter data.
