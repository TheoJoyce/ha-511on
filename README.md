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

The integration covers the data Ontario 511 publishes through its API. Coverage grows with each release; the release notes list what each version supports.

- **Live traffic:** events and closures, construction projects, road conditions, alerts and traffic cameras
- **Travel facilities:** ferry services, rest areas, truck rest areas, service centres, travel information centres, carpool lots and transit hubs
- **Commercial vehicles and road features:** inspection stations, seasonal load restrictions, HOT/HOV lanes and roundabouts

Use the entities in automations, for example to get a notification about driving conditions on your commute, with a camera preview.

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
3. Enter your Ontario 511 API key.

The key is checked against Ontario 511 before the integration is added.

## Rate limits and updates

Ontario 511 allows 10 calls every 60 seconds per key. The integration enforces this limit itself, across every entry that uses the same key, and it can't be changed.

Update intervals are fixed and stay well under the limit. Live data, such as events and road conditions, refreshes every few minutes. Data that rarely changes, such as rest areas and roundabouts, refreshes much less often.

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
