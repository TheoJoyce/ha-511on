// Loads the real map card in happy-dom, with a fake MapLibre beside it.
//
// The card imports MapLibre relative to its own URL, so a copy of the card is
// placed in a temporary directory next to fake-maplibre.mjs, renamed to
// maplibre-gl.mjs. The card's code runs unchanged.

import { copyFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { GlobalRegistrator } from "@happy-dom/global-registrator";

const here = dirname(fileURLToPath(import.meta.url));
const frontend = join(
  here,
  "..",
  "..",
  "custom_components",
  "ontario_511",
  "frontend"
);

GlobalRegistrator.register({
  url: "http://localhost:8123/",
  // The card links MapLibre's stylesheet; there's nothing to style here.
  settings: {
    disableCSSFileLoading: true,
    handleDisabledFileLoadingAsSuccess: true,
  },
});

const dir = mkdtempSync(join(tmpdir(), "ontario-511-card-"));
copyFileSync(
  join(frontend, "ontario-511-map-card.js"),
  join(dir, "ontario-511-map-card.js")
);
copyFileSync(join(here, "fake-maplibre.mjs"), join(dir, "maplibre-gl.mjs"));

await import(pathToFileURL(join(dir, "ontario-511-map-card.js")).href);
// The card waits for the frontend's root element before registering.
export const definedBeforeFrontend = Boolean(
  customElements.get("ontario-511-map-card")
);
customElements.define("home-assistant", class extends HTMLElement {});
await new Promise((resolve) => setTimeout(resolve, 100));
export const fake = await import(pathToFileURL(join(dir, "maplibre-gl.mjs")).href);

export const CARD = "ontario-511-map-card";

// Lets the card's awaited dynamic import and promise chains finish.
export const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

export const state = (entityId, value, attributes = {}) => ({
  entity_id: entityId,
  state: value,
  attributes: { friendly_name: entityId, ...attributes },
});

export const makeHass = (states, { entities = {}, darkMode = false } = {}) => ({
  states: Object.fromEntries(states.map((s) => [s.entity_id, s])),
  entities,
  themes: { darkMode },
  config: { latitude: 43.65, longitude: -79.38 },
  formatEntityState: (s) => `${s.state} (formatted)`,
});

// Creates a card in the document, sets it up and lets its map load.
export const createCard = async (config, hass) => {
  const card = document.createElement(CARD);
  document.body.appendChild(card);
  card.setConfig(config);
  card.hass = hass;
  await settle();
  const map = fake.maps.at(-1);
  map.loadStyle();
  return { card, map };
};
