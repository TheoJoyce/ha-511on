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

  // Fitted once, around the route, its local roads and the markers.
  assert.equal(map.fits.length, 1);
  assert.equal(map.fits[0].bounds.points.length, 6);
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
  assert.deepEqual(form.data, { title: "Commute", entities: [EVENT, ROUTE] });

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
