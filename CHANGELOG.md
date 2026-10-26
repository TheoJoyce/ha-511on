# Changelog

Changes to the Ontario 511 integration for Home Assistant, newest first. It's community-maintained and not affiliated with or endorsed by Ontario 511, the Ministry of Transportation, or the Government of Ontario. Versions follow [Semantic Versioning](https://semver.org/); alpha, beta and rc versions are pre-releases.

## [0.1.0-alpha.3] - Unreleased

In progress; this section is written before release.

## [0.1.0-alpha.2] - 2026-10-26

> [!WARNING]
> This is an alpha. Entities, options and stored settings may change between releases. This one changes several entities and attributes: check **Upgrading from 0.1.0-alpha.1** below before updating.

### Requirements

- Home Assistant 2026.10.0 or later
- Your own Ontario 511 API key ([request one](https://511on.ca/developers/doc) while logged in at 511on.ca)

### What's new

- **Commutes follow the roads.** Routes now come from [OpenStreetMap's routing service](https://routing.openstreetmap.de/) and run door to door along real roads, not straight lines to the nearest highway. Up to three sensible routes are found: items along all of them are generated, and the fastest is the commute's route, which its corridor and summaries follow. A commute's settings can **Plan the route again**.
- **Ontario 511 map card.** Home Assistant's map card can't draw lines, so the integration adds its own card (find it under **Add card**). It draws a commute's route, the other routes faded, items and nearest events as markers, and zones.
- **Every field of the data.** Each item's Ontario 511 fields are now entities of their own, the Home Assistant way, instead of attributes. Values with more than two states are status sensors: rest area **Status**, seasonal load **Status** and road **Visibility**. **Road condition** now includes drifting snow. Granular and duplicate entities are disabled by default. Events and alerts carry every field in their lists, on nearest events and in new-event and new-alert data.
- **Simpler areas.** One form: the map's pin and radius circle (up to 50 km; a radius of 0 is fine for a commute's start or end). Optionally choose a Home Assistant **Area** for every device it creates, and **Create a matching zone**.
- **Nearest events** are always the nearest three: **Nearest event 1** to **3** on every area and commute.
- **Single items** have their own settings form, to choose their Home Assistant area.

### Upgrading from 0.1.0-alpha.1

- **Commutes:** each commute set up with 0.1.0-alpha.1 gets a repair (Settings → Repairs). Open it and select **Submit** to plan it along roads. Existing items stay, and items along the new routes are added.
- **Attributes became entities.** For example, a rest area's amenities, a ferry's operator and website, a service centre's road and amenities, a carpool lot's facilities, and a camera's road and direction are now sensors and binary sensors on the same device. Automations that read those attributes need to use the new entities.
- **Rest area Open** and **seasonal load Load restrictions** stay, but the new **Status** sensors are the main entities now. On new installs these two binary sensors are disabled by default.
- **Visibility** is now Good, Fair or Poor (Unknown when not reported); the feed's text is in its `reported` attribute.
- **Nearest event** slots above three are removed. Empty slots are Unknown instead of unavailable.

### OpenStreetMap services

- **Commute routes:** the two areas' centres are sent to OpenStreetMap's routing service (OSRM, run by FOSSGIS), only when you submit a commute form or the repair. If routing is unavailable, you can use Ontario 511's highways only and plan again later. Routes © OpenStreetMap contributors.
- **Address search:** a typed address is sent to [OpenStreetMap Nominatim](https://nominatim.org/), only when you submit the area form. Map data © OpenStreetMap contributors.
- **Map card:** map tiles come from [VersaTiles](https://versatiles.org/) (OpenStreetMap data).

The README explains how the integration follows each service's usage policy.

### Known limitations

- Road condition names have only been seen for bare, wet and "No Report" roads so far; winter conditions are matched by keywords and will be checked against real winter data.
- Items belong to the area or commute that created them, so overlapping areas and commutes don't share items. This will be reworked.

## [0.1.0-alpha.1] - 2026-10-25

First alpha.

> [!WARNING]
> This is an alpha. Entities, options and stored settings may change between releases, and you may need to remove and re-add the integration.

### Requirements

- Home Assistant 2026.10.0 or later
- Your own Ontario 511 API key ([request one](https://511on.ca/developers/doc) while logged in at 511on.ca)

### What's included

- **Areas:** drag a pin and radius circle on the map (or type an address), then confirm the radius in kilometres. Items nearest the centre are generated.
- **Commutes** between two areas: routes are worked out over Ontario 511's road network (up to three), and summaries cover both areas and the route.
- **Every Ontario 511 data type:** road conditions, events and closures, construction, alerts, traffic cameras, ferry terminals, rest areas, truck rest areas, service centres, seasonal load restrictions, carpool lots, transit hubs, inspection stations, roundabouts, HOT/HOV lanes and travel information centres.
- **Summaries for automations:** worst road condition, hazardous conditions, full closure, event counts, region alerts, and "New event" / "New alert" event entities.
- **On the map:** items carry their location, and "Nearest event" entities put the closest events on the map.
- **Rate limits built in:** every request goes through one limiter (10 requests a minute, Ontario 511's limit), data is cached, and "Update entity" reuses fresh data.

### Address search

Typing an address uses [OpenStreetMap Nominatim](https://nominatim.org/); the address is sent to OpenStreetMap. Map data © OpenStreetMap contributors. See the README for how the integration follows the Nominatim usage policy.

### Known limitations

- Road condition names have only been seen for bare, wet and "No Report" roads so far; winter conditions are matched by keywords and will be checked against real winter data.
- Commute routes use provincial highways only; trips on local roads use a corridor around the straight line between the areas.
