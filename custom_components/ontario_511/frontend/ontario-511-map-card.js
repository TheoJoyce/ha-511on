// Ontario 511 map card: commute routes, items and events on a map.
//
// Home Assistant's map card can't draw lines, so this card draws any entity
// with a `route` attribute (an Ontario 511 commute's Route sensor) as a line,
// with its `alternatives` faded and any straight `local` stand-ins dashed, and
// any entity with a latitude and longitude as a marker. It uses MapLibre
// GL (bundled next to this file) and the same free OpenStreetMap vector tiles
// (VersaTiles) as Home Assistant's own map.

const CARD_TYPE = "ontario-511-map-card";
const STYLES = {
  light: "https://tiles.versatiles.org/assets/styles/colorful/style.json",
  dark: "https://tiles.versatiles.org/assets/styles/colorful-dark/style.json",
};
const ROUTES = "o511-routes";
const LOCAL = "o511-local";
const ALTERNATIVES = "o511-alternatives";
const ZONES = "o511-zones";
const PINS = "o511-pins";
const KM_PER_DEGREE = 111.32;

let mapLibre;
const loadMapLibre = () =>
  (mapLibre ??= import(new URL("./maplibre-gl.mjs", import.meta.url).href));

const entityId = (entry) => (typeof entry === "string" ? entry : entry.entity);

// A circle as a polygon, for zones; MapLibre has no geographic circles.
const circle = (lat, lon, radiusM, steps = 64) => {
  const ring = [];
  const dLat = radiusM / 111320;
  const dLon = radiusM / (111320 * Math.cos((lat * Math.PI) / 180));
  for (let i = 0; i <= steps; i++) {
    const angle = (i / steps) * 2 * Math.PI;
    ring.push([lon + dLon * Math.cos(angle), lat + dLat * Math.sin(angle)]);
  }
  return ring;
};

// Distance from a point to a line segment, on a flat projection around the
// point, which is accurate enough at the distances a corridor covers.
const kmToSegment = ([lat, lon], [aLat, aLon], [bLat, bLon]) => {
  const xScale = KM_PER_DEGREE * Math.cos((lat * Math.PI) / 180);
  const ax = (aLon - lon) * xScale;
  const ay = (aLat - lat) * KM_PER_DEGREE;
  const dx = (bLon - aLon) * xScale;
  const dy = (bLat - aLat) * KM_PER_DEGREE;
  const lengthSquared = dx * dx + dy * dy;
  const t = lengthSquared
    ? Math.max(0, Math.min(1, -(ax * dx + ay * dy) / lengthSquared))
    : 0;
  return Math.hypot(ax + t * dx, ay + t * dy);
};

const inCircle = (point, { center, radius_km: radius }) =>
  radius > 0 && kmToSegment(point, center, center) <= radius;

const nearLine = (point, line, km) => {
  for (let i = 1; i < line.length; i++) {
    if (kmToSegment(point, line[i - 1], line[i]) <= km) {
      return true;
    }
  }
  return false;
};

// Whether a point is in an area, or on a commute: its corridor or either area.
const inScope = (point, scope) =>
  scope.type === "area"
    ? inCircle(point, scope)
    : scope.areas.some((area) => inCircle(point, area)) ||
      scope.lines.some((line) => nearLine(point, line, scope.corridor_km));

class Ontario511MapCard extends HTMLElement {
  static getConfigElement() {
    return document.createElement(`${CARD_TYPE}-editor`);
  }

  // Suggests the first commute's Route sensor and the nearest events on the
  // same summary device.
  static getStubConfig(hass) {
    const ours = Object.values(hass.entities ?? {}).filter(
      (entry) => entry.platform === "ontario_511"
    );
    const route = ours.find(
      (entry) => hass.states[entry.entity_id]?.attributes.route
    );
    if (!route) {
      return { entities: [] };
    }
    const nearby = ours.filter(
      (entry) =>
        entry.device_id === route.device_id &&
        entry.entity_id !== route.entity_id &&
        typeof hass.states[entry.entity_id]?.attributes.latitude === "number"
    );
    return {
      entities: [route.entity_id, ...nearby.map((entry) => entry.entity_id)],
    };
  }

  setConfig(config) {
    if (config.entities !== undefined && !Array.isArray(config.entities)) {
      throw new Error("entities must be a list");
    }
    const entities = config.entities ?? [];
    const ids = JSON.stringify([
      entities.map(entityId),
      config.scope ?? null,
      config.nearby ?? true,
    ]);
    if (ids !== this._ids) {
      this._ids = ids;
      this._fitted = false;
    }
    this._config = { ...config, entities };
    this._seen = undefined;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._update();
  }

  getCardSize() {
    return 7;
  }

  getGridOptions() {
    return { columns: 12, rows: 6, min_columns: 6, min_rows: 4 };
  }

  connectedCallback() {
    this._map?.resize();
  }

  // Builds the card once; later config changes (every edit in the card
  // editor) only update the title and what's drawn, keeping the map.
  _render() {
    if (!this.shadowRoot) {
      this._build();
    }
    const title = this.shadowRoot.querySelector(".title");
    title.textContent = this._config.title ?? "";
    title.hidden = !this._config.title;
    this._update();
  }

  _build() {
    this.attachShadow({ mode: "open" });
    this._markers = new Map();
    this.shadowRoot.innerHTML = `
      <link rel="stylesheet" href="${new URL("./maplibre-gl.css", import.meta.url).href}">
      <style>
        :host { display: block; height: 100%; }
        ha-card { height: 100%; display: flex; flex-direction: column; overflow: hidden; }
        .title { padding: 12px 16px 8px; font-size: var(--ha-card-header-font-size, 24px); }
        .map { flex: 1; min-height: 300px; }
        .marker {
          width: 14px; height: 14px; border-radius: 50%; cursor: pointer;
          border: 2px solid var(--card-background-color, #fff);
          box-shadow: 0 0 3px rgba(0, 0, 0, 0.6);
        }
      </style>
      <ha-card>
        <div class="title" hidden></div>
        <div class="map"></div>
      </ha-card>`;
    new ResizeObserver(() => this._map?.resize()).observe(
      this.shadowRoot.querySelector(".map")
    );
  }

  async _update() {
    if (!this._hass || !this._config || !this.shadowRoot) {
      return;
    }
    this._loadScopes();
    if (!this._map) {
      await this._createMap();
      return;
    }
    const dark = Boolean(this._hass.themes?.darkMode);
    if (dark !== this._dark) {
      this._dark = dark;
      this._seen = undefined;
      this._styleReady = false;
      this._map.setStyle(STYLES[dark ? "dark" : "light"]);
      return;
    }
    // Redraw only when a shown entity or a zone has changed.
    const states = this._states();
    if (
      this._seen &&
      states.length === this._seen.length &&
      states.every((state, i) => state === this._seen[i])
    ) {
      return;
    }
    // Not map.isStyleLoaded(): it can still be false when style.load fires,
    // while sprites and fonts finish, and then nothing would draw.
    if (!this._styleReady) {
      return;
    }
    this._seen = states;
    this._draw();
  }

  async _createMap() {
    if (this._creating) {
      return;
    }
    this._creating = true;
    try {
      const lib = await loadMapLibre();
      const container = this.shadowRoot.querySelector(".map");
      if (this._map || !container) {
        return;
      }
      this._lib = lib;
      this._styleReady = false;
      this._dark = Boolean(this._hass.themes?.darkMode);
      this._map = new lib.Map({
        container,
        style: STYLES[this._dark ? "dark" : "light"],
        center: [this._hass.config.longitude, this._hass.config.latitude],
        zoom: 9,
        dragRotate: false,
        pitchWithRotate: false,
        touchPitch: false,
        attributionControl: { compact: true },
      });
      this._map.addControl(
        new lib.NavigationControl({ showCompass: false }),
        "top-left"
      );
      // Fires again after a theme change replaces the style.
      this._map.on("style.load", () => {
        this._styleReady = true;
        this._seen = undefined;
        this._update();
      });
    } finally {
      this._creating = false;
    }
  }

  // Areas and commutes change rarely: fetched again when the entity registry
  // or a commute's route does.
  async _loadScopes() {
    if (!this._hass.callWS) {
      return;
    }
    const routes = (this._scopes ?? [])
      .map((scope) => this._hass.states[scope.route_entity]?.attributes.route)
      .filter(Boolean);
    const unchanged =
      this._scopesEntities === this._hass.entities &&
      this._scopesRoutes?.length === routes.length &&
      routes.every((route, i) => route === this._scopesRoutes[i]);
    if (this._scopesLoaded && unchanged) {
      return;
    }
    this._scopesLoaded = true;
    this._scopesEntities = this._hass.entities;
    this._scopesRoutes = routes;
    try {
      const { scopes } = await this._hass.callWS({
        type: "ontario_511/map_scopes",
      });
      this._scopesRoutes = scopes
        .map((scope) => this._hass.states[scope.route_entity]?.attributes.route)
        .filter(Boolean);
      // A registry change usually leaves these alone: keep the user's view.
      const json = JSON.stringify(scopes);
      if (json === this._scopesJson) {
        return;
      }
      this._scopesJson = json;
      this._scopes = scopes;
      this._nearCache = new Map();
      this._fitted = false;
      this._seen = undefined;
      this._update();
    } catch {
      // Listed entities still draw without the integration's scopes.
      this._scopes ??= [];
    }
  }

  // The chosen area or commute; else the commutes whose Route is listed; else
  // everything when no entities are listed.
  _activeScopes() {
    const scopes = this._scopes ?? [];
    if (this._config.scope) {
      return scopes.filter((scope) => scope.id === this._config.scope);
    }
    const listed = new Set(this._config.entities.map(entityId));
    if (!listed.size) {
      return scopes;
    }
    return scopes.filter((scope) => listed.has(scope.route_entity));
  }

  _isNear(id, point, scopes) {
    this._nearCache ??= new Map();
    const key = `${id}|${point[0]}|${point[1]}|${scopes.map((s) => s.id).join()}`;
    let near = this._nearCache.get(key);
    if (near === undefined) {
      near = scopes.some((scope) => inScope(point, scope));
      this._nearCache.set(key, near);
    }
    return near;
  }

  // What's drawn: listed entities, the active commutes' routes, then every
  // located Ontario 511 entity in the active scopes.
  _entries() {
    const entries = [...this._config.entities];
    const shown = new Set(entries.map(entityId));
    const add = (id) => {
      if (!shown.has(id)) {
        shown.add(id);
        entries.push(id);
      }
    };
    const scopes = this._activeScopes();
    scopes.forEach((scope) => scope.route_entity && add(scope.route_entity));
    if (this._config.nearby === false || !scopes.length) {
      return entries;
    }
    for (const entry of Object.values(this._hass.entities ?? {})) {
      if (entry.platform !== "ontario_511") {
        continue;
      }
      const attributes = this._hass.states[entry.entity_id]?.attributes;
      if (
        typeof attributes?.latitude === "number" &&
        typeof attributes.longitude === "number" &&
        this._isNear(
          entry.entity_id,
          [attributes.latitude, attributes.longitude],
          scopes
        )
      ) {
        add(entry.entity_id);
      }
    }
    return entries;
  }

  _states() {
    const shown = this._entries().map(
      (entry) => this._hass.states[entityId(entry)]
    );
    const zones = Object.keys(this._hass.states)
      .filter((id) => id.startsWith("zone."))
      .sort()
      .map((id) => this._hass.states[id]);
    return [...shown, ...zones];
  }

  _color(name, fallback) {
    return getComputedStyle(this).getPropertyValue(name).trim() || fallback;
  }

  _draw() {
    const map = this._map;
    const routes = [];
    const alternatives = [];
    const local = [];
    const points = [];
    const pins = [];
    const line = (coordinates) => ({
      type: "Feature",
      properties: {},
      geometry: {
        type: "LineString",
        coordinates: coordinates.map(([lat, lon]) => [lon, lat]),
      },
    });
    for (const entry of this._entries()) {
      const state = this._hass.states[entityId(entry)];
      if (!state) {
        continue;
      }
      const { route, latitude, longitude } = state.attributes;
      if (Array.isArray(route)) {
        routes.push(...route.map(line));
        alternatives.push(...(state.attributes.alternatives ?? []).map(line));
        local.push(...(state.attributes.local ?? []).map(line));
        pins.push(
          ...(state.attributes.pins ?? []).map(([lat, lon]) => ({
            type: "Feature",
            properties: {},
            geometry: { type: "Point", coordinates: [lon, lat] },
          }))
        );
      } else if (typeof latitude === "number" && typeof longitude === "number") {
        points.push({ entry, state, lngLat: [longitude, latitude] });
      }
    }
    const zones = Object.values(this._hass.states)
      .filter(
        (state) =>
          state.entity_id.startsWith("zone.") &&
          typeof state.attributes.latitude === "number" &&
          state.attributes.radius
      )
      .map((state) => ({
        type: "Feature",
        properties: {},
        geometry: {
          type: "Polygon",
          coordinates: [
            circle(
              state.attributes.latitude,
              state.attributes.longitude,
              state.attributes.radius
            ),
          ],
        },
      }));

    this._setSource(ZONES, zones);
    this._setSource(ALTERNATIVES, alternatives);
    this._setSource(LOCAL, local);
    this._setSource(ROUTES, routes);
    this._setSource(PINS, pins);
    if (!map.getLayer(ZONES)) {
      const accent = this._color("--accent-color", "#ff9800");
      map.addLayer({
        id: ZONES,
        type: "fill",
        source: ZONES,
        paint: { "fill-color": accent, "fill-opacity": 0.15 },
      });
      // Other routes items were generated along: under the main one, faded.
      map.addLayer({
        id: ALTERNATIVES,
        type: "line",
        source: ALTERNATIVES,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": this._color("--primary-color", "#03a9f4"),
          "line-width": 4,
          "line-opacity": 0.35,
        },
      });
      // Straight lines standing in for local roads: dashed.
      map.addLayer({
        id: LOCAL,
        type: "line",
        source: LOCAL,
        layout: { "line-cap": "round" },
        paint: {
          "line-color": this._color("--primary-color", "#03a9f4"),
          "line-width": 3,
          "line-opacity": 0.7,
          "line-dasharray": [2, 2],
        },
      });
      map.addLayer({
        id: `${ROUTES}-casing`,
        type: "line",
        source: ROUTES,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#ffffff", "line-width": 8, "line-opacity": 0.8 },
      });
      map.addLayer({
        id: ROUTES,
        type: "line",
        source: ROUTES,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": this._color("--primary-color", "#03a9f4"),
          "line-width": 5,
        },
      });
      // The pins a custom route goes through.
      map.addLayer({
        id: PINS,
        type: "circle",
        source: PINS,
        paint: {
          "circle-radius": 5,
          "circle-color": "#ffffff",
          "circle-stroke-width": 3,
          "circle-stroke-color": this._color("--primary-color", "#03a9f4"),
        },
      });
    }
    this._drawMarkers(points);

    const lines = [...routes, ...alternatives, ...local];
    if (!this._fitted && (lines.length || points.length)) {
      const bounds = new this._lib.LngLatBounds();
      lines.forEach((f) =>
        f.geometry.coordinates.forEach((c) => bounds.extend(c))
      );
      points.forEach((p) => bounds.extend(p.lngLat));
      map.fitBounds(bounds, { padding: 40, maxZoom: 14, duration: 0 });
      this._fitted = true;
    }
  }

  _setSource(id, features) {
    const data = { type: "FeatureCollection", features };
    const source = this._map.getSource(id);
    if (source) {
      source.setData(data);
    } else {
      this._map.addSource(id, { type: "geojson", data });
    }
  }

  _drawMarkers(points) {
    const keep = new Set();
    for (const { entry, state, lngLat } of points) {
      const id = state.entity_id;
      keep.add(id);
      let marker = this._markers.get(id);
      if (!marker) {
        const element = document.createElement("div");
        element.className = "marker";
        element.addEventListener("click", () => {
          this.dispatchEvent(
            new CustomEvent("hass-more-info", {
              detail: { entityId: id },
              bubbles: true,
              composed: true,
            })
          );
        });
        marker = new this._lib.Marker({ element }).setLngLat(lngLat).addTo(this._map);
        this._markers.set(id, marker);
      }
      marker.setLngLat(lngLat);
      const element = marker.getElement();
      element.style.background =
        (typeof entry === "object" && entry.color) ||
        ("event_id" in state.attributes
          ? this._color("--error-color", "#db4437")
          : this._color("--primary-color", "#03a9f4"));
      const name = state.attributes.friendly_name ?? id;
      const value = this._hass.formatEntityState?.(state) ?? state.state;
      element.title = `${name}: ${value}`;
    }
    for (const [id, marker] of this._markers) {
      if (!keep.has(id)) {
        marker.remove();
        this._markers.delete(id);
      }
    }
  }
}

const ALL_SCOPES = "all";
const schema = (scopes) => [
  { name: "title", selector: { text: {} } },
  {
    name: "scope",
    selector: {
      select: {
        mode: "dropdown",
        options: [
          { value: ALL_SCOPES, label: "Automatic" },
          ...scopes.map((scope) => ({ value: scope.id, label: scope.title })),
        ],
      },
    },
  },
  { name: "nearby", selector: { boolean: {} } },
  { name: "entities", selector: { entity: { multiple: true } } },
];
const LABELS = {
  title: "Title",
  scope: "Area or commute",
  nearby: "Show everything nearby",
  entities: "Extra entities",
};
const HELPERS = {
  scope:
    "Automatic follows the commutes whose Route sensor is listed below, or all your areas and commutes if nothing is listed.",
  nearby:
    "Shows every Ontario 511 entity with a location inside the area or the commute's corridor, whichever area or commute it was added for.",
  entities:
    "Always shown. A commute's Route sensor draws its route, with the other routes its items come from faded. Other entities with a location are markers.",
};

class Ontario511MapCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = config;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _render() {
    if (!this._hass || !this._config) {
      return;
    }
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.schema = schema([]);
      this._form.computeLabel = (field) => LABELS[field.name];
      this._form.computeHelper = (field) => HELPERS[field.name];
      this._form.addEventListener("value-changed", (ev) => this._changed(ev));
      this.appendChild(this._form);
      this._hass
        .callWS?.({ type: "ontario_511/map_scopes" })
        .then(({ scopes }) => {
          this._form.schema = schema(scopes);
        })
        .catch(() => {});
    }
    this._form.hass = this._hass;
    this._form.data = {
      title: this._config.title ?? "",
      scope: this._config.scope ?? ALL_SCOPES,
      nearby: this._config.nearby ?? true,
      entities: (this._config.entities ?? []).map(entityId),
    };
  }

  _changed(ev) {
    ev.stopPropagation();
    const { title, entities, scope, nearby } = ev.detail.value;
    // Keep per-entity options (such as a colour) for entities still listed.
    const previous = new Map(
      (this._config.entities ?? []).map((entry) => [entityId(entry), entry])
    );
    const config = {
      ...this._config,
      entities: (entities ?? []).map((id) => previous.get(id) ?? id),
    };
    if (title) {
      config.title = title;
    } else {
      delete config.title;
    }
    if (scope && scope !== ALL_SCOPES) {
      config.scope = scope;
    } else {
      delete config.scope;
    }
    if (nearby === false) {
      config.nearby = false;
    } else {
      delete config.nearby;
    }
    this._config = config;
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config },
        bubbles: true,
        composed: true,
      })
    );
  }
}

const register = () => {
  const registry = window.customElements;
  if (registry.get(CARD_TYPE)) {
    return;
  }
  registry.define(CARD_TYPE, Ontario511MapCard);
  registry.define(`${CARD_TYPE}-editor`, Ontario511MapCardEditor);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: CARD_TYPE,
    name: "Ontario 511 map",
    description: "Commute routes, Ontario 511 items and nearest events on a map.",
    preview: true,
    documentationURL: "https://github.com/TheoJoyce/ha-511on#map-card",
  });
};

// Home Assistant loads this module alongside its own bundles, which then put
// their own custom element registry in place of window.customElements. A card
// defined before that is invisible to the frontend, so wait for the frontend's
// root element to be defined first.
const whenFrontendReady = (check) =>
  window.customElements.get("home-assistant")
    ? check()
    : setTimeout(() => whenFrontendReady(check), 50);

whenFrontendReady(register);
