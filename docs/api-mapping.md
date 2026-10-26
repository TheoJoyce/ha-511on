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
4. **Each resource then plays one of three roles:**
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

- **Commutes first:** items within the corridor around the route, in route order.
- **Then areas:** items within the radius, nearest to the centre first, skipping items a commute already has.
- **Caps** for this first pass: 40 road segments, 25 cameras, 25 of every other type. **Add item** adds any single item later, without a cap.

The chosen items are stored by ID in the area's or commute's settings, so they don't change when the feed does. An item that disappears from the feed makes its entities **unavailable** until it comes back.

## Item resources, field by field

Reading the tables:

- **Entity** means the field is that entity's state.
- **Attribute** means it's a state attribute of the named entity. Attributes only hold what explains a state, plus the location for the map.
- **Internal** means it's used to work something out but not shown on its own.
- **Not used** means it's ignored, with the reason given.

Every item's **main entity** (marked ★) carries `latitude` and `longitude` attributes, so the item appears on maps. These are excluded from the recorder because they don't change.

### Road conditions → Road segment device

| API field | Shown as |
|---|---|
| `RoadwayName` | **Road** sensor; part of the device name ("Hwy 401: …") and the segment's ID |
| `LocationDescription` | Device name; part of the ID (the API has no `Id`) |
| `Condition` (list of text) | ★ **Road condition** enum sensor: the worst of the list, ranked good, wet, partly covered, covered, icy, closed. Text it doesn't recognise, and "No Report", is Unknown. The original text is in the `reported` attribute |
| `Visibility` | **Visibility** sensor |
| `Drifting` | **Drifting snow** binary sensor |
| `LastUpdated` | **Last updated** timestamp sensor (diagnostic) |
| `Region` | Internal: decides which alerts apply to areas and commutes containing the segment |
| `EncodedPolyline` | Internal: thinned to about one point per 250 m. Used for the map location (the point halfway along), for which events are on the segment, for area and corridor matching, and as the road network for routing |

Worked out from those fields:

| Entity | How |
|---|---|
| **Hazardous** (binary, safety) | On when the condition is partly covered or worse, drifting snow is reported, or visibility is "Poor" |
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
| `Views[].Id` | Internal |
| `SourceId`, `SortOrder` | Not used: internal identifiers of Ontario 511's source systems and web map |

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
| `Region` | Not shown: its region codes (for example "ON-EC") don't match the alert regions |
| `Twitter` | Not used: a social media link, not travel data |

### Rest areas and truck rest areas → Rest area / Truck rest area device

Both endpoints have the same fields and the same entities.

| API field | Shown as |
|---|---|
| `Name` | Device name; part of the ID (the API has no `Id`) |
| `Status` | ★ **Open** binary sensor (opening): on for "Open", off for "Closed", otherwise Unknown. The original text is in the `reported` attribute |
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
| `Region` | Not shown |
| `Mode` | Not used: undocumented, and only in some items (for example "Trucker") |

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
| `Region` | Not shown |
| `Title` | Not used: a generic label ("service centre") |

### Seasonal load restrictions → Seasonal load segment device

| API field | Shown as |
|---|---|
| `SegmentName` | Device name; part of the ID (the API has no `Id`) |
| `Status` | ★ **Load restrictions** binary sensor (off for "No restrictions…", on for other text mentioning restrictions, otherwise Unknown), and the **Status** sensor with the text itself |
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
| `Region` | Not shown |

### Transit hubs → Transit hub device

| API field | Shown as |
|---|---|
| `Id`, `Name` | Device ID and name |
| `Address` | ★ **Address** sensor |
| `Website` | **Website** sensor (disabled by default) |
| `Region` | Not shown |

### Inspection stations → Inspection station device

| API field | Shown as |
|---|---|
| `Id`, `Name` | Device ID and name |
| `Location` | ★ **Location** sensor |
| `Highway` | **Road** sensor |
| `Direction` | **Direction** sensor |
| `ContactInformation` | **Contact** sensor |
| `Region` | Not shown |

### Roundabouts → Roundabout device

| API field | Shown as |
|---|---|
| `Name` | Device name; part of the ID (the API has no `Id`) |
| `Type` | ★ **Type** sensor |
| `Location` | **Location** sensor; part of the ID |
| `Region` | Not shown |

### HOT/HOV lanes → HOT/HOV lane device

| API field | Shown as |
|---|---|
| `Id` | Device ID |
| `Roadway`, `Name` | Device name ("Highway 403 Eastbound HOV"); `Roadway` is also the ★ **Road** sensor |
| `EncodedPolyline` | Internal: parsed, but not drawn yet |
| `Region` | Not shown |

### Travel information centres → Travel information centre device

| API field | Shown as |
|---|---|
| `Id`, `Name` | Device ID and name |
| `Open` | ★ **Hours** sensor |
| `Address` | **Address** sensor |
| `PhoneNumber` | **Phone** sensor |
| `Email` | **Email** sensor |
| `Region` | Not shown |

`Region` isn't shown for any item: its only use is matching alerts, which is done through road segments.

## Feeds: events, construction, alerts

Events and construction projects start and end all the time. Making each one a device would create and delete entities constantly. So instead they're attached to the stable things they affect, and counted and listed there.

### Which ones count

- **Active only:** an event or project counts once its `StartDate` has passed and until its `PlannedEndDate`. Planned future work doesn't count.
- **On a road segment:** each active event or project is attached to the nearest road segment within 2 km of its `Latitude`/`Longitude`. Live data puts 95% within 150 m. A segment only counts those inside the area or commute it belongs to, so a 100 km segment doesn't report events far outside it.
- **In an area:** within the radius of the centre, nearest first.
- **On a commute:** door to door. That means everything in the start area, then everything within the corridor width of the route (highway stretches and the local roads to and from them) in route order, then the destination area, with each event counted once.
- **Alerts** have no location. An alert applies to an area or commute when its `Regions` include the region of any road segment there, or when it lists no region. "Northwest" and "Northwestern" are treated as the same region.

### Where they appear

| Entity | Device | Shows |
|---|---|---|
| **Events**, **Construction** | Road segment | Count; first 10 in `items` |
| **Full closure** | Road segment | On while one closes all lanes |
| **New event** | Road segment | Fires for each new event on the segment |
| **Events**, **Construction**, **Alerts** | Area or commute summary | Count; first 10 in `items` (alerts with high importance first) |
| **Full closure** | Area or commute summary | On while any event or project in scope closes all lanes |
| **New event**, **New alert** | Area or commute summary | Fire for each new event or alert in scope |
| **Nearest event 1** to **3** | Area or commute summary | The three nearest active events (in route order for commutes). State is the description; details and location are attributes, so they show on maps. Unknown when there are fewer than three |

The summary also has **Worst road condition** (the worst condition among its segments, with those segments listed), **Hazardous conditions** (on while any of its segments is hazardous) and, for commutes, **Route** (see [Map card](design.md#map-card)).

### Event and construction fields

Both endpoints share one model.

| API field | Shown as |
|---|---|
| `ID` | `event_id` in lists and new-event data; how "new" is decided |
| `RoadwayName`, `DirectionOfTravel` | `road`, `direction` in lists, nearest events and new-event data |
| `Description` | `description` in the same places; the state of **Nearest event N** |
| `EventType` | `type` in the same places |
| `IsFullClosure` | `full_closure` in the same places; drives **Full closure** |
| `PlannedEndDate` | `planned_end` in lists and nearest events; decides whether it's active |
| `StartDate` | Internal: decides whether it's active |
| `LanesAffected` | `lanes_affected` on **Nearest event N** |
| `Latitude`, `Longitude` | Location of **Nearest event N**; distance (`distance_km`); attaching to segments; area and corridor matching |
| `EventSubType`, `Severity`, `Organization`, `Comment`, `Impact`, `Recurrence`, `Restrictions`, `Reported`, `LastUpdated` | Parsed, but not shown yet |
| `SourceId`, `LinkId`, `LatitudeSecondary`, `LongitudeSecondary`, `EncodedPolyline`, `RecurrenceSchedules` | Not used: source system identifiers, and geometry that's empty or repeats the location in sample data |

### Alert fields

| API field | Shown as |
|---|---|
| `Id` | How "new" is decided |
| `Message` | `message` in the **Alerts** list and new-alert data |
| `Regions` | `regions` in the list; decides where it applies |
| `HighImportance` | `high_importance` in the list; high-importance alerts are listed first |
| `Notes`, `StartTime`, `EndTime` | Parsed, but not shown yet. Every alert the feed returns is treated as current |
| `LastUpdated`, `SendNotification` | Not used |

## Location data that isn't from Ontario 511

- **Address search** sends a typed address to OpenStreetMap Nominatim, only when the area form is submitted (see the README).
- **Commute routes** are worked out locally from the road conditions polylines (see [Routing](design.md#routing)). Nothing is sent anywhere.
- **The map card** loads map tiles from VersaTiles (OpenStreetMap data). It sends the map area being viewed, not any Ontario 511 data.
