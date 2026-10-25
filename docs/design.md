# Design

How the integration models Ontario 511 data in Home Assistant. This is the reference for what gets generated, how it's organised, and how fresh each kind of data is kept. Rules in `AGENTS.md` (rate limits, compliance) still apply.

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
| **Area** | Name, centre and radius (drag a pin and circle on the map, then confirm the radius in km; max 50 km) | Items of the chosen data types, nearest to the centre first, and a summary device |
| **Commute** | The two areas it runs between (an existing one, or a new area added in the same flow), corridor width (default 5 km, max 20 km). Routes between the areas' centres are worked out automatically; the segments can be adjusted by reconfiguring | Items of the chosen data types within the corridor, and a summary device covering both areas and the route (door to door) |
| **Item** | A data type, then one item from a list sorted by distance | That one item. No cap applies |

Setup chains: API key, then a first area ("Home", 25 km), then optionally a commute, which first adds the area it goes to ("Work").

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

Home Assistant defines a device as "a physical device … or a service". Each generated item is a **service device**, and each area and commute has a **summary device**. Devices belong to the subentry that generated them, so the integration page shows them under that area or commute.

Values that change get their own entities. Details go in attributes; lists in attributes are capped and excluded from the recorder.

| Device | Entities |
|---|---|
| Road segment | Road condition (ranked enum), Visibility, Drifting, Events (count; list in attributes), Construction (count; list in attributes), Full closure (binary), New event (event entity) |
| Camera | One camera entity per view |
| Ferry terminal | Status, Estimated return to service (timestamp) |
| Rest area, truck rest area | Status (open or closed) |
| Service centre | Fuel (the field can include times fuel isn't available) |
| Seasonal load | Restriction status |
| Carpool lot, transit hub, inspection station, roundabout, HOV lane, travel info centre | One entity whose state is the most useful field (for example a carpool lot's parking spaces), other fields as attributes |
| Area or commute summary | Worst road condition, Hazardous conditions (binary), Full closure (binary), Events, Construction, New event, Nearest event 1…N, Alerts for their region and New alert |

- **Event entities** fire when something new appears (a new event on a segment, a new alert). They are stable entities whose state is the time of the last event.
- **Attaching events to segments:** each event or construction project is assigned to the nearest road segment within 2 km of it. Live data puts 95% of events within 150 m of a segment. Anything further away counts only towards the area or commute summary.
- **Region alerts:** alerts list the regions they affect. An area's or commute's regions come from the road segments in it; alerts listing no region count everywhere.

### Map

The map card shows any entity with numeric `latitude` and `longitude` attributes, and the built-in Map dashboard adds them automatically. Each item's main entity carries its location, so items appear on the map and move if the data moves.

**Nearest event slots** put events on the map without temporary entities. Each area and commute has a fixed number of slot entities ("Nearest event 1", "Nearest event 2", …). Slot N shows the Nth nearest active event: its location, description and details. When there are fewer events than slots, the extra slots are unavailable. The number of slots is set when creating the area or commute (default 5) and can be changed later.

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

## Address search

Areas can be placed by typing an address, looked up with OpenStreetMap Nominatim (`geocode.py`). Its [usage policy](https://operations.osmfoundation.org/policies/nominatim/) sets the rules:

- Only a user submitting the area form triggers a lookup; no autocomplete and nothing periodic. Limits apply to the integration as a whole, across every install.
- At most one request per second per Home Assistant instance; identical addresses are answered from a cache.
- Requests send the integration's own User-Agent (`ha-511on/<version> (+repository URL)`).
- OpenStreetMap must be able to switch the service off without a software update: before a lookup, `geocoding.json` on the repository's `main` branch is checked (cached for an hour). If it isn't `{"enabled": true}`, or can't be read, lookups are refused.
- The form shows OpenStreetMap attribution and says the address is sent to OpenStreetMap.
- Results outside Ontario are rejected.

## Data quirks

- 27% of road segments report "No Report"; that is treated as unknown, not as a good condition.
- Ferry status values don't match the documented casing ("Not In Service" vs "Not in Service"); statuses are compared case-insensitively.
- Some seasonal load statuses have trailing spaces.
- Road segments, rest areas, roundabouts and seasonal loads have no `Id` field. Their IDs are derived from road and description (road segments), name, road and direction (rest areas), name and location (roundabouts), or segment name, route and coordinates (seasonal loads, where one name and route pair is duplicated).
- Some events are published with a flipped longitude sign; coordinates outside Ontario are treated as missing.
- October data only shows bare, wet and "No Report" road conditions. The condition ranking must handle values it hasn't seen, and is to be checked against winter data.

## Later

- Easier ways to define a commute, for example importing a route from Google Maps.
- Linking areas to commutes.
- Checking the road condition ranking against winter data.
