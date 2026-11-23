// Tests for the Ontario 511 map card, run with `npm test` (node:test).

import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import {
  CARD,
  createCard,
  definedBeforeFrontend,
  fake,
  makeHass,
  settle,
  state,
} from "./harness.mjs";

const ROUTE = "sensor.home_to_work_route";
const EVENT = "sensor.home_to_work_nearest_event_1";
const CAMERA = "camera.highway_401_view_1";
const ROUTE_STATE = state(ROUTE, "42.0", {
  route: [
    [
      [43.7, -79.6],
      [43.7, -79.5],
    ],
  ],
  alternatives: [
    [
      [43.7, -79.6],
      [43.75, -79.55],
      [43.7, -79.5],
    ],
  ],
  local: [
    [
      [43.71, -79.61],
      [43.7, -79.6],
    ],
  ],
});
const EVENT_STATE = state(EVENT, "Collision", {
  latitude: 43.72,
  longitude: -79.45,
  event_id: "323209",
});
const CAMERA_STATE = state(CAMERA, "idle", { latitude: 43.71, longitude: -79.55 });
const HOME_ZONE = state("zone.home", "0", {
  latitude: 43.65,
  longitude: -79.38,
  radius: 100,
});

afterEach(() => {
  document.body.innerHTML = "";
});

test("waits for the frontend's registry before registering", () => {
  // Defining it earlier would put it where the frontend can't see it.
  assert.equal(definedBeforeFrontend, false);
});

test("registers the card and its editor for the card picker", () => {
  assert.ok(customElements.get(CARD));
  assert.ok(customElements.get(`${CARD}-editor`));
  const entry = window.customCards.find((c) => c.type === CARD);
  assert.equal(entry.name, "Ontario 511 map");
  assert.equal(entry.preview, true);
});

test("suggests the first commute route and located entities on its device", () => {
  const Card = customElements.get(CARD);
  const hass = makeHass([ROUTE_STATE, EVENT_STATE, CAMERA_STATE], {
    entities: {
      [ROUTE]: { entity_id: ROUTE, platform: "ontario_511", device_id: "summary" },
      [EVENT]: { entity_id: EVENT, platform: "ontario_511", device_id: "summary" },
      // On another device: an item, not part of this commute's summary.
      [CAMERA]: { entity_id: CAMERA, platform: "ontario_511", device_id: "camera" },
    },
  });
  assert.deepEqual(Card.getStubConfig(hass), { entities: [ROUTE, EVENT] });
  assert.deepEqual(Card.getStubConfig(makeHass([CAMERA_STATE])), {
    entities: [],
  });
});

test("rejects entities that aren't a list", () => {
  const card = document.createElement(CARD);
  assert.throws(() => card.setConfig({ entities: ROUTE }), /must be a list/);
});

test("draws routes as lines, located entities as markers, and zones", async () => {
  const hass = makeHass([ROUTE_STATE, EVENT_STATE, CAMERA_STATE, HOME_ZONE]);
  const { map } = await createCard(
    { entities: [ROUTE, EVENT, CAMERA, "sensor.missing"] },
    hass
  );

  assert.equal(map.options.style.endsWith("/colorful/style.json"), true);
  assert.deepEqual(map.options.center, [-79.38, 43.65]);
  const [route] = map.sources["o511-routes"].data.features;
  // GeoJSON is longitude first; the attribute is latitude first.
  assert.deepEqual(route.geometry.coordinates, [
    [-79.6, 43.7],
    [-79.5, 43.7],
  ]);
  const [local] = map.sources["o511-local"].data.features;
  assert.deepEqual(local.geometry.coordinates, [
    [-79.61, 43.71],
    [-79.6, 43.7],
  ]);
  assert.deepEqual(map.layers["o511-local"].paint["line-dasharray"], [2, 2]);
  const [alternative] = map.sources["o511-alternatives"].data.features;
  assert.deepEqual(alternative.geometry.coordinates[1], [-79.55, 43.75]);
  assert.ok(map.layers["o511-alternatives"].paint["line-opacity"] < 1);
  // Alternatives are drawn under the main route.
  const order = Object.keys(map.layers);
  assert.ok(order.indexOf("o511-alternatives") < order.indexOf("o511-routes"));
  const [zone] = map.sources["o511-zones"].data.features;
  assert.equal(zone.geometry.type, "Polygon");
  assert.ok(map.layers["o511-routes"] && map.layers["o511-zones"]);

  assert.deepEqual(
    map.markers.map((m) => m.lngLat),
    [
      [-79.45, 43.72],
      [-79.55, 43.71],
    ]
  );
  const [event, camera] = map.markers.map((m) => m.getElement());
  assert.equal(event.style.background, "#db4437");
  assert.equal(camera.style.background, "#03a9f4");
  assert.equal(event.title, `${EVENT}: Collision (formatted)`);

  // Fitted once, around the routes, local roads and markers.
  assert.equal(map.fits.length, 1);
  assert.equal(map.fits[0].bounds.points.length, 9);
});

test("a marker opens the entity's more-info dialog", async () => {
  const { card, map } = await createCard(
    { entities: [EVENT] },
    makeHass([EVENT_STATE])
  );
  const opened = [];
  card.addEventListener("hass-more-info", (ev) => opened.push(ev));
  map.markers[0].getElement().click();
  assert.equal(opened.length, 1);
  assert.deepEqual(opened[0].detail, { entityId: EVENT });
  assert.equal(opened[0].bubbles && opened[0].composed, true);
});

test("redraws only when a shown entity or zone changes", async () => {
  const states = [ROUTE_STATE, EVENT_STATE, HOME_ZONE];
  const { card, map } = await createCard(
    { entities: [ROUTE, EVENT] },
    makeHass(states)
  );
  const routes = map.sources["o511-routes"];

  card.hass = makeHass(states);
  assert.equal(routes.updates, 0);

  // An unrelated entity changing doesn't redraw either.
  card.hass = makeHass([...states, state("light.kitchen", "on")]);
  assert.equal(routes.updates, 0);

  const moved = state(EVENT, "Collision", {
    latitude: 43.8,
    longitude: -79.3,
    event_id: "323209",
  });
  card.hass = makeHass([ROUTE_STATE, moved, HOME_ZONE]);
  assert.equal(routes.updates, 1);
  assert.deepEqual(map.markers[0].lngLat, [-79.3, 43.8]);
  assert.equal(map.fits.length, 1);
});

test("an entity that loses its location loses its marker", async () => {
  const { card, map } = await createCard(
    { entities: [EVENT] },
    makeHass([EVENT_STATE])
  );
  card.hass = makeHass([state(EVENT, "unknown")]);
  assert.equal(map.markers[0].removed, true);
});

test("follows Home Assistant's dark mode", async () => {
  const { card, map } = await createCard(
    { entities: [ROUTE] },
    makeHass([ROUTE_STATE])
  );
  card.hass = makeHass([ROUTE_STATE], { darkMode: true });
  assert.equal(map.style.endsWith("/colorful-dark/style.json"), true);
  assert.deepEqual(map.sources, {});

  // The new style's load draws everything again.
  map.loadStyle();
  assert.equal(map.sources["o511-routes"].data.features.length, 1);
});

test("an entity's colour can be set", async () => {
  const { map } = await createCard(
    { entities: [{ entity: EVENT, color: "orange" }] },
    makeHass([EVENT_STATE])
  );
  assert.equal(map.markers[0].getElement().style.background, "orange");
});

test("the title is text, not HTML", async () => {
  const { card } = await createCard(
    { title: "<img src=x onerror=alert(1)>", entities: [] },
    makeHass([])
  );
  const title = card.shadowRoot.querySelector(".title");
  assert.equal(title.hidden, false);
  assert.equal(title.textContent, "<img src=x onerror=alert(1)>");
  assert.equal(card.shadowRoot.querySelector("img"), null);
});

test("waits for the map style before drawing", async () => {
  const card = document.createElement(CARD);
  document.body.appendChild(card);
  card.setConfig({ entities: [ROUTE] });
  card.hass = makeHass([ROUTE_STATE]);
  await settle();
  const map = fake.maps.at(-1);
  card.hass = makeHass([ROUTE_STATE]);
  assert.deepEqual(map.sources, {});
  map.loadStyle();
  assert.equal(map.sources["o511-routes"].data.features.length, 1);
});

test("the editor keeps per-entity options and drops an empty title", () => {
  const editor = document.createElement(`${CARD}-editor`);
  document.body.appendChild(editor);
  editor.setConfig({
    type: `custom:${CARD}`,
    title: "Commute",
    entities: [{ entity: EVENT, color: "orange" }, ROUTE],
  });
  editor.hass = makeHass([]);
  const form = editor.querySelector("ha-form");
  assert.deepEqual(form.data, {
    title: "Commute",
    scope: "all",
    nearby: true,
    entities: [EVENT, ROUTE],
  });

  const changes = [];
  editor.addEventListener("config-changed", (ev) => changes.push(ev.detail.config));
  form.dispatchEvent(
    new CustomEvent("value-changed", {
      detail: { value: { title: "", entities: [EVENT, CAMERA] } },
    })
  );
  assert.deepEqual(changes, [
    {
      type: `custom:${CARD}`,
      entities: [{ entity: EVENT, color: "orange" }, CAMERA],
    },
  ]);
});

test("the editor saves a chosen scope and only a turned-off nearby", () => {
  const editor = document.createElement(`${CARD}-editor`);
  document.body.appendChild(editor);
  editor.setConfig({ type: `custom:${CARD}`, entities: [] });
  editor.hass = makeHass([]);
  const form = editor.querySelector("ha-form");
  const changes = [];
  editor.addEventListener("config-changed", (ev) => changes.push(ev.detail.config));
  const change = (value) =>
    form.dispatchEvent(
      new CustomEvent("value-changed", {
        detail: { value: { title: "", entities: [], ...value } },
      })
    );
  change({ scope: "commute-1", nearby: false });
  change({ scope: "all", nearby: true });
  assert.deepEqual(changes, [
    { type: `custom:${CARD}`, entities: [], scope: "commute-1", nearby: false },
    { type: `custom:${CARD}`, entities: [] },
  ]);
});

const NEAR = "sensor.near_camera";
const FAR = "sensor.far_rest_area";
const IN_AREA = "sensor.in_area_ferry";
const OTHER = "sensor.other_integration";
const COMMUTE_SCOPE = {
  id: "commute-1",
  title: "Home to Work",
  type: "commute",
  route_entity: ROUTE,
  corridor_km: 2,
  lines: [
    [
      [43.7, -79.6],
      [43.7, -79.5],
    ],
  ],
  areas: [{ center: [43.9, -79.9], radius_km: 3 }],
};
const AREA_SCOPE = {
  id: "area-1",
  title: "Cottage",
  type: "area",
  center: [44.5, -79.0],
  radius_km: 5,
};
const located = (id, latitude, longitude) =>
  state(id, "ok", { latitude, longitude });
const scopedHass = (scopes = [COMMUTE_SCOPE, AREA_SCOPE]) => {
  const hass = makeHass(
    [
      ROUTE_STATE,
      // About 1 km from the route.
      located(NEAR, 43.709, -79.55),
      located(FAR, 45.0, -81.0),
      located(IN_AREA, 44.51, -79.01),
      located(OTHER, 43.7, -79.55),
    ],
    {
      entities: Object.fromEntries([
        ...[ROUTE, NEAR, FAR, IN_AREA].map((id) => [
          id,
          { entity_id: id, platform: "ontario_511" },
        ]),
        [OTHER, { entity_id: OTHER, platform: "other" }],
      ]),
    }
  );
  hass.calls = [];
  hass.callWS = async (message) => {
    hass.calls.push(message);
    return { scopes };
  };
  return hass;
};
const markerIds = (hass, map) =>
  map.markers
    .filter((m) => !m.removed)
    .map(({ lngLat: [lon, lat] }) =>
      Object.values(hass.states).find(
        (s) => s.attributes.latitude === lat && s.attributes.longitude === lon
      ).entity_id
    )
    .sort();
const createScoped = async (config, hass = scopedHass()) => {
  const { card, map } = await createCard(config, hass);
  await settle();
  return { card, map, hass };
};

test("a listed route shows what's near that commute, without listing it", async () => {
  const { map, hass } = await createScoped({ entities: [ROUTE] });
  assert.deepEqual(hass.calls, [{ type: "ontario_511/map_scopes" }]);
  assert.deepEqual(markerIds(hass, map), [NEAR]);
});

test("with nothing listed, every area and commute is shown", async () => {
  const { map, hass } = await createScoped({ entities: [] });
  assert.deepEqual(markerIds(hass, map), [IN_AREA, NEAR].sort());
  assert.equal(map.sources["o511-routes"].data.features.length, 1);
});

test("a chosen area shows only what's inside it", async () => {
  const { map, hass } = await createScoped({ entities: [], scope: "area-1" });
  assert.deepEqual(markerIds(hass, map), [IN_AREA]);
  assert.equal(map.sources["o511-routes"].data.features.length, 0);
});

test("a chosen commute draws its route and includes its areas", async () => {
  const hass = scopedHass();
  hass.states[FAR] = located(FAR, 43.91, -79.91);
  const { map } = await createScoped({ entities: [], scope: "commute-1" }, hass);
  assert.equal(map.sources["o511-routes"].data.features.length, 1);
  assert.deepEqual(markerIds(hass, map), [FAR, NEAR].sort());
});

test("nearby can be turned off, leaving what's listed", async () => {
  const { map, hass } = await createScoped({
    entities: [ROUTE, FAR],
    nearby: false,
  });
  assert.deepEqual(markerIds(hass, map), [FAR]);
});

test("a list without a route keeps showing only that list", async () => {
  const { map, hass } = await createScoped({ entities: [FAR] });
  assert.deepEqual(markerIds(hass, map), [FAR]);
});

test("a registry change with the same scopes keeps the user's view", async () => {
  const { card, map, hass } = await createScoped({ entities: [ROUTE] });
  const fits = map.fits.length;
  card.hass = { ...hass, entities: { ...hass.entities } };
  await settle();
  await settle();
  assert.equal(hass.calls.length, 2);
  assert.equal(map.fits.length, fits);
});

test("a failed scope request still draws what's listed", async () => {
  const hass = scopedHass();
  hass.callWS = async () => {
    throw new Error("unknown_command");
  };
  const { map } = await createScoped({ entities: [ROUTE, FAR] }, hass);
  assert.equal(map.sources["o511-routes"].data.features.length, 1);
  assert.deepEqual(markerIds(hass, map), [FAR]);
});

test("only the latest scope reply is used", async () => {
  const hass = scopedHass();
  const replies = [];
  hass.callWS = () => new Promise((resolve) => replies.push(resolve));
  const { card, map } = await createScoped({ entities: [ROUTE] }, hass);
  card.hass = { ...hass, entities: { ...hass.entities } };
  await settle();
  assert.equal(replies.length, 2);
  replies[1]({ scopes: [COMMUTE_SCOPE] });
  await settle();
  replies[0]({ scopes: [] });
  await settle();
  assert.deepEqual(markerIds(hass, map), [NEAR]);
});

test("a custom route's pins are drawn", async () => {
  const hass = scopedHass();
  hass.states[ROUTE] = state(ROUTE, "42.0", {
    ...ROUTE_STATE.attributes,
    pins: [[43.72, -79.55]],
  });
  const { map } = await createScoped({ entities: [ROUTE] }, hass);
  const [pin] = map.sources["o511-pins"].data.features;
  assert.deepEqual(pin.geometry.coordinates, [-79.55, 43.72]);
});

test("draws on style.load even if MapLibre still reports loading", async () => {
  const card = document.createElement(CARD);
  document.body.appendChild(card);
  card.setConfig({ entities: [ROUTE, EVENT] });
  card.hass = makeHass([ROUTE_STATE, EVENT_STATE]);
  await settle();
  const map = fake.maps.at(-1);
  // Sprites and fonts can still be loading when style.load fires.
  map.loadStyle({ reportLoaded: false });
  assert.equal(map.sources["o511-routes"].data.features.length, 1);
  assert.equal(map.markers.length, 1);
});

test("a config change keeps the map and redraws what's listed", async () => {
  const card = document.createElement(CARD);
  document.body.appendChild(card);
  card.setConfig({ entities: [ROUTE, EVENT] });
  card.hass = makeHass([ROUTE_STATE, EVENT_STATE]);
  await settle();
  const map = fake.maps.at(-1);
  const created = fake.maps.length;
  map.loadStyle();
  assert.equal(map.fits.length, 1);

  // The card editor calls setConfig on every edit.
  card.setConfig({ title: "Commute", entities: [ROUTE, EVENT] });
  card.hass = makeHass([ROUTE_STATE, EVENT_STATE]);
  await settle();
  assert.equal(fake.maps.length, created);
  assert.equal(map.removed, false);
  assert.equal(card.shadowRoot.querySelector(".title").textContent, "Commute");
  // Same entities: the user's view isn't reset.
  assert.equal(map.fits.length, 1);

  card.setConfig({ title: "Commute", entities: [ROUTE] });
  card.hass = makeHass([ROUTE_STATE, EVENT_STATE]);
  await settle();
  assert.equal(fake.maps.length, created);
  assert.equal(map.markers[0].removed, true);
  assert.equal(map.sources["o511-routes"].data.features.length, 1);
  assert.equal(map.fits.length, 2);
});
