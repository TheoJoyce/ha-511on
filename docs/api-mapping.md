# API mapping

How every piece of Ontario 511 data is fetched, kept and shown. For the reasoning behind the data model, see [`design.md`](design.md); for the endpoints, see the [API documentation](https://511on.ca/developers/doc).

## From API to entity

```
Ontario 511 endpoint ──► coordinator ──► parsed items ──┬──► item devices (sensors, binary sensors, cameras)
(one call = the whole      (one per        (models.py)   │
 province)                 resource)                     ├──► feeds: events, construction, alerts
                                                         │      └─► counted per road segment, area and commute
                                                         └──► road segments: also the road network for routing
```

1. **One call per resource, never per item.** Each endpoint returns everything Ontario 511 has for the province. Only resources that some area, commute or item needs are fetched.
2. **One coordinator per resource** keeps the whole parsed response. Every entity of that resource reads from it, so a hundred cameras cost the same one call as one camera.
3. **Parsing** (`models.py`) turns each API item into a model. It never trusts types: text is trimmed, `"Y"`/`"Yes"`/`"N"`/`"No"` become true or false, numbers in strings become numbers, Unix times become timestamps, and coordinates outside Ontario's bounding box are dropped. A missing or unrecognised value is `None`, which Home Assistant shows as **Unknown**.
4. **Every field is exposed, and multi-state values are statuses.** A value with more than two possible states is a status (enum) sensor, never squeezed into a yes/no. Combined statuses (such as **Road condition** including drifting snow) keep each part as its own entity too. The granular and duplicate ones are disabled by default to avoid clutter, so you can turn them on in the entity settings when you want finer control.
5. **Each resource then plays one of three roles:**
   - **Items** become devices with one entity per field (13 resources).
   - **Feeds** come and go, so they don't become devices. They're counted and listed on the road segments, areas and commutes they fall in (events, construction, alerts).
   - **Road segments** are items too, and also the network that commutes are routed over.

## How often each resource is fetched

Every call goes through the instance-wide rate limiter (10 calls per 61 seconds; see [Rate limits](../AGENTS.md#rate-limits)). **Refresh** is the background polling interval. **Fresh for** is how long a response counts as current: Home Assistant's **Update entity** action inside that window reuses it instead of calling the API. All 16 together poll about 0.8 calls a minute.

| Resource | Endpoint | Role | Refresh | Fresh for |
|---|---|---|---|---|
| Events | `/api/v2/get/event` | Feed | 3 min | 2 min |
| Alerts | `/api/v2/get/alerts` | Feed | 5 min | 2 min |
| Road conditions | `/api/v3/get/roadconditions` | Item, routing | 10 min | 5 min |
| Ferry terminals | `/api/v2/get/ferryterminals` | Item | 10 min | 5 min |
| Construction projects | `/api/v2/get/constructionprojects` | Feed | 30 min | 15 min |
| Rest areas | `/api/v2/get/allrestareas` | Item | 1 h | 30 min |
| Truck rest areas | `/api/v2/get/truckrestareas` | Item | 1 h | 30 min |
| Service centres | `/api/v2/get/servicecentres` | Item | 1 h | 30 min |
| Seasonal load restrictions | `/api/v2/get/seasonalloadapi` | Item | 6 h | 1 h |
| Traffic cameras | `/api/v2/get/cameras` | Item | 24 h | 6 h |
| Carpool lots | `/api/v2/get/carpoollots` | Item | 24 h | 6 h |
| Transit hubs | `/api/v2/get/transithub` | Item | 24 h | 6 h |
| Inspection stations | `/api/v2/get/inspectionstations` | Item | 24 h | 6 h |
| Roundabouts | `/api/v2/get/roundabouts` | Item | 24 h | 6 h |
| HOT/HOV lanes | `/api/v2/get/hovlanes` | Item | 24 h | 6 h |
| Travel information centres | `/api/v2/get/informationcenter` | Item | 24 h | 6 h |

Camera **images** don't come from the API. They're fetched from each view's `Url` when a dashboard shows them, and cached for 60 seconds.

## Which items exist

The data type checkboxes on an area or commute are used once, to choose items. For each chosen type:

- **Commutes first:** items within the corridor around any of the commute's routes, in route order.
- **Then areas:** items within the radius, nearest to the centre first, skipping items a commute already has.
- **Caps** for this first pass: 40 road segments, 25 cameras, 25 of every other type. **Add item** adds any single item later, without a cap.

The chosen items are stored by ID in the area's or commute's settings, so they don't change when the feed does. An item that disappears from the feed makes its entities **unavailable** until it comes back.

## Item resources, field by field

Every field of every item resource is exposed as an entity, except the IDs and names that identify the device itself and the polylines (see Geometry below). In the tables:

- An entity in **bold** shows that field as its state. A **status** is an enum sensor: its states are listed, translated in the UI, and anything else is Unknown.
- **Attribute** means it's a state attribute of the named entity. Attributes only hold what explains a state, plus the location for the map.
- **Disabled by default** means the entity exists but starts off. Turn it on from the entity's settings.
- **Internal** means it's used to work something out rather than shown; only polylines are.

Every item's **main entity** (marked ★) carries `latitude` and `longitude` attributes, so the item appears on maps. These are excluded from the recorder because they don't change.

### Road conditions → Road segment device

| API field | Shown as |
|---|---|
| `RoadwayName` | **Road** sensor; part of the device name ("Hwy 401: …") and the segment's ID |
| `LocationDescription` | Device name; part of the ID (the API has no `Id`) |
| `Condition` (list of text) | ★ **Road condition** status: the overall condition, ranked good, wet, partly covered, snow covered, drifting snow, icy, closed. It's the worst of the reported conditions, raised to at least drifting snow while `Drifting` is reported. Text it doesn't recognise, and "No Report", is Unknown. Attributes: `reported` (the original text) and `drifting`. Also **Surface condition** (the same ranking from the reported conditions alone, without drifting snow) and **Reported conditions** (the text itself), both disabled by default |
| `Visibility` | **Visibility** status: good, fair or poor; anything else is Unknown, with the original text in `reported` |
| `Drifting` | Part of **Road condition**; also the **Drifting snow** binary sensor (disabled by default) |
| `LastUpdated` | **Last updated** timestamp sensor (diagnostic) |
| `Region` | **Region** sensor (disabled by default); decides which alerts apply to areas and commutes containing the segment |
| `EncodedPolyline` | Internal: thinned to about one point per 250 m. Used for the map location (the point halfway along), for which events are on the segment, for area and corridor matching, and as the road network for routing |

Worked out from those fields:

| Entity | How |
|---|---|
| **Hazardous** (binary, safety) | On when **Road condition** is partly covered or worse (drifting snow included) or visibility is poor |
| **Events**, **Construction** | Active events and construction projects attached to this segment (see [Feeds](#feeds-events-construction-alerts)), counted, with the first 10 listed in an `items` attribute |
| **Full closure** (binary, problem) | On while any of those closes all lanes |
| **New event** (event entity) | Fires when an event appears on the segment |

### Traffic cameras → Traffic camera device

| API field | Shown as |
|---|---|
| `Id` | Device ID |
| `Location` | Device name |
| `Roadway` | **Road** sensor |
| `Direction` | **Direction** sensor ("Unknown" in the feed is Unknown) |
| `Source` | **Source** sensor (diagnostic, disabled by default) |
| `Latitude`, `Longitude` | ★ Location, on the camera entity for view 1 |
| `Views[]` | One **View N** camera entity per view |
| `Views[].Url` | The image the camera entity shows (not an API call; cached 60 s) |
| `Views[].Status` | The view's camera entity is unavailable unless the status is "Enabled" |
| `Views[].Description` | `description` attribute of that view's camera entity |
| `Views[].Id` | `view_id` attribute of that view's camera entity |
| `SourceId` | **Source ID** sensor (diagnostic, disabled by default) |
| `SortOrder` | **Sort order** sensor (diagnostic, disabled by default) |

### Ferry terminals → Ferry terminal device

| API field | Shown as |
|---|---|
| `Id` | Device ID |
| `LocationDescription` | Device name |
| `Status` | ★ **Status** enum sensor: in service, in service off schedule, not in service. "No Status Available", or anything else, is Unknown. The original text is in the `reported` attribute |
| `Details` | **Details** sensor |
| `EstimatedReturnToServiceTime` | **Estimated return to service** timestamp sensor |
| `EstimatedOutOfServiceTime` | **Estimated out of service** timestamp sensor (disabled by default) |
| `OperatedBy` | **Operated by** sensor |
| `Website` | **Website** sensor (disabled by default) |
| `Region` | **Region** sensor (disabled by default). Ferry regions are codes (for example "ON-EC") that don't match the alert regions |
| `Twitter` | **Twitter** sensor (disabled by default) |

### Rest areas and truck rest areas → Rest area / Truck rest area device

Both endpoints have the same fields and the same entities.

| API field | Shown as |
|---|---|
| `Name` | Device name; part of the ID (the API has no `Id`) |
| `Status` | ★ **Status** status: open or closed; anything else is Unknown, with the original text in `reported`. Also the **Open** binary sensor (disabled by default) |
| `TruckParking` | **Truck parking** binary sensor |
| `Lavatory` | **Washrooms** binary sensor |
| `Accessible` | **Accessible** binary sensor |
| `Fuel` | **Fuel** binary sensor |
| `Type` | **Type** sensor |
| `Roadway` | **Road** sensor; part of the ID |
| `Direction` | **Direction** sensor; part of the ID |
| `Location` | **Location** sensor |
| `Open` (dates, for example "May 15 to November 15") | **Season** sensor |
| `FoodServices` | **Food services** sensor |
| `Comments` | **Comments** sensor |
| `Mode` | **Mode** sensor (disabled by default): Driver, Trucker or Both in current data, and only in some items |
| `Region` | **Region** sensor (disabled by default) |

### Service centres → Service centre device

| API field | Shown as |
|---|---|
| `Id` | Device ID |
| `Name` | Device name |
| `FuelProvider` | ★ **Fuel** sensor |
| `FoodServices` | **Food services** sensor |
| `Amenities` | **Amenities** sensor |
| `AccessVia` | **Access** sensor |
| `Roadway` | **Road** sensor |
| `CommercialParking` | **Commercial parking spaces** sensor |
| `Website` | **Website** sensor (disabled by default) |
| `Title` | **Title** sensor (disabled by default): a generic label such as "Service Centre" |
| `Region` | **Region** sensor (disabled by default) |

### Seasonal load restrictions → Seasonal load segment device

| API field | Shown as |
|---|---|
| `SegmentName` | Device name; part of the ID (the API has no `Id`) |
| `Status` | ★ **Status** status: no restrictions or restrictions in effect (other text mentioning restrictions counts as in effect); anything else is Unknown, with the original text in `reported`. Also the **Load restrictions** binary sensor (disabled by default) |
| `RouteDescription` | **Route** sensor; part of the ID |
| `Date` | **Date** sensor (date) |
| `Latitude`, `Longitude` | ★ Location; part of the ID (one name and route pair appears twice) |

### Carpool lots → Carpool lot device

| API field | Shown as |
|---|---|
| `Id` | Device ID |
| `Highway`, `LocationDescription` | Device name ("Hwy 407 at Trafalgar Rd"); `Highway` is also the **Road** sensor |
| `ParkingSpaces` | ★ **Parking spaces** sensor |
| `AccessibleSpaces` | **Accessible parking spaces** sensor |
| `Interchange` | **Interchange** sensor |
| `EnterLotFrom` | **Entrance** sensor |
| `Bike` | **Bike rack** binary sensor |
| `Lights` | **Lighting** binary sensor |
| `Transit` | **Transit connection** binary sensor |
| `Region` | **Region** sensor (disabled by default) |

### Transit hubs → Transit hub device

| API field | Shown as |
|---|---|
| `Id`, `Name` | Device ID and name |
| `Address` | ★ **Address** sensor |
| `Website` | **Website** sensor (disabled by default) |
| `Region` | **Region** sensor (disabled by default) |

### Inspection stations → Inspection station device

| API field | Shown as |
|---|---|
| `Id`, `Name` | Device ID and name |
| `Location` | ★ **Location** sensor |
| `Highway` | **Road** sensor |
| `Direction` | **Direction** sensor |
| `ContactInformation` | **Contact** sensor |
| `Region` | **Region** sensor (disabled by default) |

### Roundabouts → Roundabout device

| API field | Shown as |
|---|---|
| `Name` | Device name; part of the ID (the API has no `Id`) |
| `Type` | ★ **Type** sensor |
| `Location` | **Location** sensor; part of the ID |
| `Region` | **Region** sensor (disabled by default) |

### HOT/HOV lanes → HOT/HOV lane device

| API field | Shown as |
|---|---|
| `Id` | Device ID |
| `Roadway`, `Name` | Device name ("Highway 403 Eastbound HOV"); `Roadway` is also the ★ **Road** sensor |
| `EncodedPolyline` | Internal: parsed, not exposed (see Geometry below) |
| `Region` | **Region** sensor (disabled by default) |

### Travel information centres → Travel information centre device

| API field | Shown as |
|---|---|
| `Id`, `Name` | Device ID and name |
| `Open` | ★ **Hours** sensor |
| `Address` | **Address** sensor |
| `PhoneNumber` | **Phone** sensor |
| `Email` | **Email** sensor |
| `Region` | **Region** sensor (disabled by default) |

Seasonal load segments and cameras have no `Region` field.

**Geometry** is exposed as each item's location. Polylines (road segments, HOT/HOV lanes, events) aren't attributes, because they're large; commute routes are, on the **Route** sensor for the map card.

## Feeds: events, construction, alerts

Events and construction projects start and end all the time. Making each one a device would create and delete entities constantly. So instead they're attached to the stable things they affect, and counted and listed there.

### Which ones count

- **Active only:** an event or project counts once its `StartDate` has passed and until its `PlannedEndDate`. Planned future work doesn't count.
- **On a road segment:** each active event or project is attached to the nearest road segment within 2 km of its `Latitude`/`Longitude`. Live data puts 95% within 150 m. A segment only counts those inside the area or commute it belongs to, so a 100 km segment doesn't report events far outside it.
- **In an area:** within the radius of the centre, nearest first.
- **On a commute:** door to door. That means everything in the start area, then everything within the corridor width of its main route (along the roads, door to door) in route order, then the destination area, with each event counted once.
- **Alerts** have no location. An alert applies to an area or commute when its `Regions` include the region of any road segment there, or when it lists no region. "Northwest" and "Northwestern" are treated as the same region.

### Where they appear

| Entity | Device | Shows |
|---|---|---|
| **Events**, **Construction** | Road segment | Count; the first 10, with every field, in `items` |
| **Full closure** | Road segment | On while one closes all lanes |
| **New event** | Road segment | Fires for each new event on the segment |
| **Events**, **Construction**, **Alerts** | Area or commute summary | Count; the first 10, with every field, in `items` (alerts with high importance first) |
| **Full closure** | Area or commute summary | On while any event or project in scope closes all lanes |
| **New event**, **New alert** | Area or commute summary | Fire for each new event or alert in scope |
| **Nearest event 1** to **3** | Area or commute summary | The three nearest active events (in route order for commutes). State is the description; every field is an attribute, including the location, so they show on maps. Unknown when there are fewer than three |

The summary also has **Worst road condition** (the worst condition among its segments, with those segments listed), **Hazardous conditions** (on while any of its segments is hazardous) and, for commutes, **Route** (see [Map card](design.md#map-card)).

### Event and construction fields

Both endpoints share one model. **Every field is in every event's details:** the `items` lists behind counts, the attributes of **Nearest event N**, and the data of **New event** events. Times are ISO 8601; a missing value is `null`.

| API field | Key in the details | Also used for |
|---|---|---|
| `ID` | `event_id` | Deciding what's new |
| `SourceId` | `source_id` | |
| `Organization` | `organization` | |
| `RoadwayName` | `road` | |
| `DirectionOfTravel` | `direction` | |
| `EventType`, `EventSubType` | `type`, `subtype` | |
| `Severity` | `severity` | |
| `Description` | `description` | State of **Nearest event N** |
| `LanesAffected` | `lanes_affected` | |
| `IsFullClosure` | `full_closure` | **Full closure** |
| `Impact`, `Comment` | `impact`, `comment` | |
| `Restrictions` | `restrictions` (only the limits that are set) | |
| `Recurrence`, `RecurrenceSchedules` | `recurrence`, `recurrence_schedules` | |
| `StartDate`, `PlannedEndDate` | `start`, `planned_end` | Deciding whether it's active |
| `Reported`, `LastUpdated` | `reported`, `last_updated` | |
| `LinkId` | `link_id` | |
| `Latitude`, `Longitude` | `latitude`, `longitude`, and `distance_km` from the area or along the commute | Maps, attaching to segments, area and corridor matching |
| `LatitudeSecondary`, `LongitudeSecondary` | `secondary_latitude`, `secondary_longitude` | |
| `EncodedPolyline` | Not exposed (see Geometry above) | |

### Alert fields

**Every field is in every alert's details:** the **Alerts** `items` list and the data of **New alert** events.

| API field | Key in the details | Also used for |
|---|---|---|
| `Id` | `alert_id` | Deciding what's new |
| `Message` | `message` | |
| `Notes` | `notes` | |
| `Regions` | `regions` | Deciding where it applies |
| `HighImportance` | `high_importance` | High-importance alerts are listed first |
| `SendNotification` | `send_notification` | |
| `StartTime`, `EndTime` | `start`, `end` | Not used to filter: every alert the feed returns counts as current |
| `LastUpdated` | `last_updated` | |

## Location data that isn't from Ontario 511

- **Address search** sends a typed address to OpenStreetMap Nominatim, only when the area form is submitted (see the README).
- **Commute routes** send the two areas' centres (and points along highway routes it missed) to OpenStreetMap's routing service, only when a commute form or repair is submitted (see [Routing](design.md#routing)). Without it, routes are worked out locally from the road conditions polylines.
- **The map card** loads map tiles from VersaTiles (OpenStreetMap data). It sends the map area being viewed, not any Ontario 511 data.
