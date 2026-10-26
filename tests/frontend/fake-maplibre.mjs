// A stand-in for MapLibre GL that records what the card asks of it.
// The harness copies it next to the card as maplibre-gl.mjs.

export const maps = [];

export class LngLatBounds {
  constructor() {
    this.points = [];
  }

  extend(point) {
    this.points.push(point);
    return this;
  }
}

export class NavigationControl {
  constructor(options) {
    this.options = options;
  }
}

export class Marker {
  constructor({ element }) {
    this.element = element;
    this.removed = false;
  }

  setLngLat(lngLat) {
    this.lngLat = lngLat;
    return this;
  }

  addTo(map) {
    map.markers.push(this);
    return this;
  }

  getElement() {
    return this.element;
  }

  remove() {
    this.removed = true;
  }
}

class Source {
  constructor(data) {
    this.data = data;
    this.updates = 0;
  }

  setData(data) {
    this.data = data;
    this.updates += 1;
  }
}

export class Map {
  constructor(options) {
    this.options = options;
    this.style = options.style;
    this.styleLoaded = false;
    this.sources = {};
    this.layers = {};
    this.markers = [];
    this.controls = [];
    this.fits = [];
    this.listeners = {};
    this.resizes = 0;
    this.removed = false;
    maps.push(this);
  }

  on(event, listener) {
    (this.listeners[event] ??= []).push(listener);
  }

  // Tests call this to finish loading the current style. Real MapLibre can
  // still report the style as not loaded when style.load fires.
  loadStyle({ reportLoaded = true } = {}) {
    this.styleLoaded = reportLoaded;
    for (const listener of this.listeners["style.load"] ?? []) {
      listener();
    }
  }

  setStyle(style) {
    this.style = style;
    this.styleLoaded = false;
    this.sources = {};
    this.layers = {};
  }

  isStyleLoaded() {
    return this.styleLoaded;
  }

  addControl(control, position) {
    this.controls.push({ control, position });
  }

  addSource(id, { data }) {
    this.sources[id] = new Source(data);
  }

  getSource(id) {
    return this.sources[id];
  }

  addLayer(layer) {
    this.layers[layer.id] = layer;
  }

  getLayer(id) {
    return this.layers[id];
  }

  fitBounds(bounds, options) {
    this.fits.push({ bounds, options });
  }

  resize() {
    this.resizes += 1;
  }

  remove() {
    this.removed = true;
  }
}
