<div align="center">

# Ontario 511 Integration

**Ontario's roads in Home Assistant: live events, closures, construction, cameras and road conditions, plus ferries, rest areas, service centres, carpool lots and more.**

[![Release](https://img.shields.io/github/v/release/TheoJoyce/ha-511on?include_prereleases&sort=semver&display_name=tag&style=flat-square&logo=github&label=release)](https://github.com/TheoJoyce/ha-511on/releases)
[![Tests](https://img.shields.io/github/actions/workflow/status/TheoJoyce/ha-511on/ci.yml?branch=main&style=flat-square&logo=githubactions&logoColor=white&label=tests)](https://github.com/TheoJoyce/ha-511on/actions/workflows/ci.yml)
[![hassfest](https://img.shields.io/github/actions/workflow/status/TheoJoyce/ha-511on/hassfest.yml?branch=main&style=flat-square&logo=homeassistant&logoColor=white&label=hassfest)](https://github.com/TheoJoyce/ha-511on/actions/workflows/hassfest.yml)
[![HACS validation](https://img.shields.io/github/actions/workflow/status/TheoJoyce/ha-511on/hacs.yml?branch=main&style=flat-square&logo=homeassistantcommunitystore&logoColor=white&label=HACS%20validation)](https://github.com/TheoJoyce/ha-511on/actions/workflows/hacs.yml)
[![Last commit](https://img.shields.io/github/last-commit/TheoJoyce/ha-511on/main?style=flat-square&label=last%20commit)](https://github.com/TheoJoyce/ha-511on/commits/main)

[![HACS](https://img.shields.io/badge/HACS-custom-41BDF5?style=flat-square&logo=homeassistantcommunitystore&logoColor=white)](https://hacs.xyz/docs/faq/custom_repositories/)
[![Home Assistant](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fraw.githubusercontent.com%2FTheoJoyce%2Fha-511on%2Fmain%2Fhacs.json&query=%24.homeassistant&prefix=%E2%89%A5%20&label=Home%20Assistant&style=flat-square&logo=homeassistant&logoColor=white&color=18BCF2)](https://www.home-assistant.io/)
[![Licence](https://img.shields.io/github/license/TheoJoyce/ha-511on?style=flat-square&label=licence)](LICENSE)
[![Data: OGL-Ontario](https://img.shields.io/badge/data-OGL%E2%80%93Ontario-2E7D32?style=flat-square)](https://www.ontario.ca/page/open-government-licence-ontario)

</div>

> [!IMPORTANT]
> This is a community-maintained integration. It is not affiliated with, endorsed by, or supported by Ontario 511, the Ministry of Transportation, or the Government of Ontario. It uses the open data that [Ontario 511](https://511on.ca) publishes.

> [!WARNING]
> This integration is in alpha. Features are incomplete, and entities and options may change between releases. Check the [release notes](https://github.com/TheoJoyce/ha-511on/releases) before updating.

## Features

Entities for the Ontario 511 data near the places you care about, not the whole province.

- **Areas:** drag a pin and a radius circle on the map (up to 50 km; Home Assistant shows the radius in metres). Items nearest the centre are generated. A radius of 0 is fine for an area you only need as a commute's start or end.
- **Commutes:** a commute runs from one area to another (for example Home to Work). Up to three sensible routes along the roads between them are found (see [Commute routes](#commute-routes)), and items along all of them are generated. The fastest is the commute's route: a corridor (default 5 km) around it counts as on the commute, and its summaries are door to door: both areas plus that route. Commute items are generated before anything else.
- **Every data type:** events and closures, construction, road conditions, alerts, traffic cameras, ferry terminals, rest areas, truck rest areas, service centres, seasonal load restrictions, carpool lots, transit hubs, inspection stations, roundabouts, HOT/HOV lanes and travel information centres.
- **No temporary entities:** events and construction come and go, so they're counted on the road segment, area or commute they fall in. Event entities fire when something new appears.
- **On the map:** each item's main entity has a location, and "Nearest event" entities put the closest events on the map.

For example: *when "Hazardous conditions" on your commute turns on, send a notification with the camera preview.*

## Entities

Each generated item is a device, grouped under the area or commute that generated it on the integration page.

| Device | Entities (in brackets: disabled by default) |
|---|---|
| Road segment | Road condition, Visibility, Road, Hazardous, Events, Construction, Full closure, New event, Last updated (diagnostic) [Surface condition, Reported conditions, Drifting snow, Region] |
| Traffic camera | One camera per view (images cached for a minute), Road, Direction [Source, Source ID, Sort order] |
| Ferry terminal | Status, Details, Estimated return to service, Operated by [Estimated out of service, Website, Twitter, Region] |
| Rest area, truck rest area | Status, Truck parking, Washrooms, Accessible, Fuel, Type, Road, Direction, Location, Season, Food services, Comments [Open, Mode, Region] |
| Service centre | Fuel, Food services, Amenities, Access, Road, Commercial parking spaces [Website, Title, Region] |
| Seasonal load segment | Status, Route, Date [Load restrictions] |
| Carpool lot | Parking spaces, Accessible parking spaces, Road, Interchange, Entrance, Bike rack, Lighting, Transit connection [Region] |
| Transit hub | Address [Website, Region] |
| Inspection station | Location, Road, Direction, Contact [Region] |
| Roundabout | Type, Location [Region] |
| HOT/HOV lane | Road [Region] |
| Travel information centre | Hours, Address, Phone, Email [Region] |
| Area or commute summary | Worst road condition, Hazardous conditions, Full closure, Events, Construction, Alerts, New event, New alert, Nearest event 1 to 3, and for commutes Route (for the [map card](#map-card)) |

Every field Ontario 511 publishes is available, so you can use any of it in automations, dashboards and templates. [docs/api-mapping.md](docs/api-mapping.md) lists every field and where it ends up.

- **Statuses, not just on/off:** values with more than two states are status sensors you can pick from in automations. **Road condition** is the overall condition: bare and dry, wet, partly covered, snow covered, drifting snow, icy or closed. Its parts are separate entities too (Surface condition, Drifting snow, Reported conditions), disabled by default to avoid clutter; turn them on from the entity's settings for finer control.
- **Events and alerts** carry every field (severity, lanes affected, impact, times, restrictions and more) in the `items` lists behind each count, on **Nearest event** entities, and in the data of **New event** and **New alert** events.
- **Hazardous** means partly covered or worse (drifting snow included), or poor visibility.
- A status shows **Unknown** when Ontario 511 has no report or sends a value it doesn't know; the feed's own text is in the `reported` attribute.
- A segment's events, construction and full closure only count what's inside the area or commute corridor it belongs to.
- Events count as active once they've started and until their planned end. Planned future events aren't counted.
- **Alerts** count when they list the region the area or commute is in.

## Requirements

- Home Assistant 2026.10.0 or later
- An Ontario 511 API key of your own (see below). The integration does not include or share one.

### Getting an API key

1. Register an account at [511on.ca](https://511on.ca/my511/register).
2. While logged in, request a developer key on the [API documentation page](https://511on.ca/developers/doc).
3. Keep the key for the setup step below.

Your use of the key is governed by Ontario 511's [Developer's Access Agreement](https://511on.ca/developers/resources).

## Installation

### HACS (recommended)

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=TheoJoyce&repository=ha-511on&category=integration)

Or add it by hand:

1. In Home Assistant, open **HACS**.
2. Open the menu (⋮) in the top right and select **Custom repositories**.
3. Enter `https://github.com/TheoJoyce/ha-511on`, choose the type **Integration**, and select **Add**.
4. Search for **Ontario 511** in HACS, open it, and select **Download**.
5. Restart Home Assistant.

### Manual

1. Download the source code from the [latest release](https://github.com/TheoJoyce/ha-511on/releases).
2. Copy the `custom_components/ontario_511` folder into the `custom_components` folder in your Home Assistant configuration directory.
3. Restart Home Assistant.

## Setup

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=ontario_511)

Or add it by hand:

1. Go to **Settings** → **Devices & services**.
2. Select **Add integration** and search for **Ontario 511**.
3. Enter your Ontario 511 API key. It's checked with one request before you continue.
4. Set up your first **area**: a name, the pin and radius circle on the map, and the data types to generate. Instead of dragging the pin you can type an address (see [Address search](#address-search)). Optionally:
   - choose an **Area** (Home Assistant's own areas, for example *Outside*) for every device the area creates, so you don't have to assign them one by one;
   - tick **Create a matching zone** to add a Home Assistant zone with the same centre and radius. The zone is yours from then on: it isn't changed or removed with the area. An area needs a radius for this.
5. Optionally add a **commute**. It starts by adding the second area it goes to (for example Work), then asks for the corridor width, what to generate, and a Home Assistant area for its devices (prefilled from the start area). Its routes along the roads are found automatically (see [Commute routes](#commute-routes)).

The data type checkboxes are only used once, to decide what to generate. If you choose more than 10 types, the rest load about a minute later because of Ontario 511's rate limit.

### After setup

On the integration page:

- **Add area**, **Add commute** or **Add item** to add more. **Add commute** links two areas you already have, or adds a new area to go to. **Add item** adds any single item, chosen from a list sorted by distance from your home.
- Open an area's or commute's menu (⋮) and select **Reconfigure** to rename it or change which events, construction and alerts it follows. Choosing a different **Area** there moves all of its devices to that Home Assistant area. For a commute you can also change the corridor width and which road segments its route uses, or tick **Plan the route again** to find new routes, for example after moving one of its areas.
- Delete an item's device to remove that item.

### Forcing a refresh

Data refreshes on its own. To get the latest data in an automation, use Home Assistant's **Update entity** action on any of the integration's entities. Data fetched in the last couple of minutes is reused instead of asking Ontario 511 again.

### Map card

Home Assistant's map card can't draw lines, so the integration includes an **Ontario 511 map** card. It's added automatically; find it under **Add card** when editing a dashboard. It suggests your first commute's **Route** sensor and its nearest events.

- An entity with a route (a commute's **Route** sensor) is drawn as a line along the roads it takes, with the other routes its items come from faded underneath.
- Any other entity with a location (items, nearest events) is a marker. Select one to open its details.
- Zones are shaded, and the map follows Home Assistant's light or dark theme.

The map uses [MapLibre GL](https://maplibre.org/), bundled with the integration, and [VersaTiles](https://versatiles.org/) vector tiles, the same free OpenStreetMap tiles as Home Assistant's own map. Map data © OpenStreetMap contributors.

```yaml
type: custom:ontario-511-map-card
title: Home to Work
entities:
  - sensor.home_to_work_route
  - sensor.home_to_work_nearest_event_1
  - entity: sensor.home_to_work_nearest_event_2
    color: orange
```

### Address search

When setting up an area you can type an address instead of dragging the pin. It's looked up with [OpenStreetMap Nominatim](https://nominatim.org/), and the map is shown again with the pin on the place found so you can check it.

- **The address you type is sent to OpenStreetMap.** Nothing is sent unless you type an address and submit the form. Map data © OpenStreetMap contributors.
- The integration follows the [Nominatim usage policy](https://operations.osmfoundation.org/policies/nominatim/): one lookup per submit, at most one per second, repeated addresses answered from a cache, and requests identify the integration.
- Address search can be switched off for everyone without an update, through [`geocoding.json`](geocoding.json) in this repository (checked at most once an hour). If it's off, or can't be checked, you can still place the pin on the map.

### Commute routes

A commute's routes are found with [OpenStreetMap's routing service](https://routing.openstreetmap.de/) (OSRM, run by FOSSGIS), so they follow real roads door to door. Up to three sensible routes are kept: the fastest and any others within 30% of its driving time, including highway routes the service didn't offer at first. Items along all of them are generated; the fastest is the commute's route, which its corridor and summaries follow. Routes © OpenStreetMap contributors.

- **The two areas' centres are sent to OpenStreetMap.** That happens only when you submit a commute form (adding a commute, or **Plan the route again** in its settings) or the repair below; never in the background.
- The integration follows the [routing service's usage policy](https://routing.openstreetmap.de/about.html): at most a few requests per submit, at most one per second, repeated routes answered from a cache, and requests identify the integration.
- Routing can be switched off for everyone without an update, through [`geocoding.json`](geocoding.json) in this repository. If it's off or can't be reached, the form offers to try again or to use Ontario 511's highways only, with straight lines between them and the start and end; you can plan the route again later.
- Commutes set up with 0.1.0-alpha.1 only used Ontario 511's highways. Home Assistant shows a repair for each: open it and select **Submit** to plan the commute along roads. Its existing items stay, and items along the new routes are added.

## Rate limits and updates

Ontario 511 allows 10 requests every 60 seconds. The integration enforces this itself, across every entry in your Home Assistant, and it can't be changed. One request covers a whole data type, so the number of entities doesn't change how many requests are made.

| Data | Refreshes every |
|---|---|
| Events | 3 minutes |
| Alerts | 5 minutes |
| Road conditions, ferry terminals | 10 minutes |
| Construction | 30 minutes |
| Rest areas, truck rest areas, service centres | hour |
| Seasonal load restrictions | 6 hours |
| Cameras (list), carpool lots, transit hubs, inspection stations, roundabouts, HOT/HOV lanes, travel information centres | day |

## Removal

1. Go to **Settings** → **Devices & services** and select **Ontario 511**.
2. Open the menu (⋮) on the entry and select **Delete**.
3. If you installed through HACS, remove the integration in HACS too, then restart Home Assistant.

## Troubleshooting

- **Download diagnostics:** on the integration's page, open the menu (⋮) and select **Download diagnostics**. Your API key is redacted.
- **Debug logging:** on the integration's page, select **Enable debug logging**, reproduce the problem, then select **Disable debug logging** to download the log.
- **Report a problem:** open an [issue](https://github.com/TheoJoyce/ha-511on/issues) and attach the diagnostics or log.

## Data licence and attribution

Contains information licensed under the [Open Government Licence – Ontario](https://www.ontario.ca/page/open-government-licence-ontario).

Data source: [Ontario 511](https://511on.ca).

## Disclaimer

Ontario 511 data is provided "as is" and "as available" without warranty of any kind. Actual road conditions may differ from the data shown.

This project is not affiliated with or endorsed by Ontario 511, the Ministry of Transportation, or the Government of Ontario.

## Licence

The code is licensed under the [Apache License 2.0](LICENSE). The licence applies to the code, not to Ontario 511 data.

## Acknowledgements

`AGENTS.md` and the skills in `.claude/skills/` are adapted from [Home Assistant Core](https://github.com/home-assistant/core) under the Apache License 2.0. Each adapted file links to its source and lists what was changed.

The map card bundles [MapLibre GL JS](https://github.com/maplibre/maplibre-gl-js) 6.14.0, under the BSD 3-Clause licence (`custom_components/ontario_511/frontend/maplibre-gl.LICENSE.txt`).
