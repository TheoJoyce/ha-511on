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

- **Areas:** pick a centre and radius (up to 50 km). Items nearest the centre are generated.
- **Commutes:** pick a start and end, confirm the road segments along the way, and set how wide a corridor counts as on the route. Commute items are generated before anything else.
- **Every data type:** events and closures, construction, road conditions, alerts, traffic cameras, ferry terminals, rest areas, truck rest areas, service centres, seasonal load restrictions, carpool lots, transit hubs, inspection stations, roundabouts, HOT/HOV lanes and travel information centres.
- **No temporary entities:** events and construction come and go, so they're counted on the road segment, area or commute they fall in. Event entities fire when something new appears.
- **On the map:** each item's main entity has a location, and "Nearest event" entities put the closest events on the map.

For example: *when "Hazardous conditions" on your commute turns on, send a notification with the camera preview.*

## Entities

Each generated item is a device, grouped under the area or commute that generated it on the integration page.

| Device | Entities |
|---|---|
| Road segment | Road condition, Visibility, Drifting snow, Hazardous, Events, Construction, Full closure, New event |
| Traffic camera | One camera per view (images cached for a minute) |
| Ferry terminal | Status, Estimated return to service, Estimated out of service (disabled by default) |
| Rest area, truck rest area | Open, with amenities as attributes |
| Service centre | Fuel, with amenities as attributes |
| Seasonal load segment | Load restrictions |
| Carpool lot, transit hub, inspection station, roundabout, HOT/HOV lane, travel information centre | One sensor with the most useful field (for example parking spaces), other details as attributes |
| Area or commute summary | Worst road condition, Hazardous conditions, Full closure, Events, Construction, Alerts, New event, New alert, Nearest event 1…N |

- **Hazardous** means worse than wet (partly covered or worse), drifting snow, or poor visibility.
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
4. Set up your first **area**: a name, the centre and radius on the map, the data types to generate, and how many "Nearest event" entities to add.
5. Optionally add a **commute**: a start and end on the map and a corridor width, then confirm the suggested road segments.

The data type checkboxes are only used once, to decide what to generate. If you choose more than 10 types, the rest load about a minute later because of Ontario 511's rate limit.

### After setup

On the integration page:

- **Add area**, **Add commute** or **Add item** to add more. **Add item** adds any single item, chosen from a list sorted by distance from your home.
- Open an area's or commute's menu (⋮) and select **Reconfigure** to rename it or change which events, construction and alerts it follows.
- Delete an item's device to remove that item.

### Forcing a refresh

Data refreshes on its own. To get the latest data in an automation, use Home Assistant's **Update entity** action on any of the integration's entities. Data fetched in the last couple of minutes is reused instead of asking Ontario 511 again.

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
