/* ios-loc front end.
 *
 * The server is the single source of truth: it reports the position it last
 * pushed to the device. This file polls that state and animates the puck
 * between two consecutive reports, so a 1 Hz stream of fixes reads as
 * continuous motion instead of a marker hopping once a second.
 *
 * The map is MapLibre over vector tiles: labels stay sharp at any zoom, the
 * dark basemap is a real style rather than an inverted light one, and the view
 * can be tilted and rotated.
 */

/* global maplibregl, L */

import {
  LANGUAGES, applyTranslations, detectLanguage, getLanguage, setLanguage, t,
} from "/static/i18n.js";

const POLL_MS = 600;
const MAX_SMOOTH_JUMP_M = 1500; // beyond this it is a teleport, not movement

const $ = (id) => document.getElementById(id);

const ICONS = {
  pin: '<path d="M12 2a7 7 0 0 0-7 7c0 5.2 7 13 7 13s7-7.8 7-13a7 7 0 0 0-7-7Z"/><circle cx="12" cy="9" r="2.3"/>',
  walk: '<circle cx="13" cy="4" r="1.8"/><path d="M13 7.5 10 12l1.5 3-1 6m3.5-9 3 2.5M11.5 15l-2.5 6"/>',
  run: '<circle cx="15" cy="4" r="1.8"/><path d="M15 7.5 11 11l2 3-2 7m4-10 4 1.5M13 14l-4 3-3-1"/>',
  bike: '<circle cx="6" cy="17" r="3.4"/><circle cx="18" cy="17" r="3.4"/><path d="m9 17 4-8h3m-7 8 5-8M14 6h3"/>',
  car: '<path d="M4 16v-3l2-5h12l2 5v3M4 16h16M4 16v2m16-2v2"/><circle cx="8" cy="16" r="1.4"/><circle cx="16" cy="16" r="1.4"/>',
  train: '<rect x="6" y="3" width="12" height="13" rx="3.5"/><path d="M6 10h12M9 20l1.5-3m4.5 3-1.5-3"/><circle cx="9.5" cy="13" r="1"/><circle cx="14.5" cy="13" r="1"/>',
  plane: '<path d="M11 3.5a1 1 0 0 1 2 0V9l8 4.5v2l-8-2.3v4l3 2v1.8l-4-1.3-4 1.3V19l3-2v-4L3 15.5v-2L11 9Z"/>',
};

// ------------------------------------------------------------------ settings

const SETTINGS_KEY = "iosloc.settings";

const DEFAULT_SETTINGS = {
  language: "auto",     // auto | en | ru
  theme: "auto",        // auto | light | dark
  mapStyle: "auto",     // auto | liberty | positron | dark | graybeard
  units: "kmh",         // kmh | mph | ms
  followCamera: true,
  showTrail: true,
  keyboardControl: true,
  allowPitch: false,
  online: false,
};

const UNITS = {
  kmh: { label: "km/h", fromKmh: (v) => v },
  mph: { label: "mph", fromKmh: (v) => v * 0.621371 },
  ms: { label: "m/s", fromKmh: (v) => v / 3.6 },
};

const settings = { ...DEFAULT_SETTINGS };

function loadSettings() {
  try {
    Object.assign(settings, JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}"));
  } catch {
    /* blocked or private storage: defaults apply */
  }
}

function saveSettings() {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
  } catch {
    /* settings just will not persist */
  }
}

function resolvedLanguage() {
  return settings.language === "auto" ? detectLanguage() : settings.language;
}

/** Apply the language everywhere: markup, then everything drawn from JS. */
function applyLanguage() {
  setLanguage(resolvedLanguage());
  applyTranslations();
  renderProfiles();
  renderFavourites();
  renderSavedRoutes();
  renderDraft();
  if (state.server) applyState(state.server);
}

function prefersDark() {
  return !window.matchMedia || window.matchMedia("(prefers-color-scheme: dark)").matches;
}

function resolvedTheme() {
  return settings.theme === "auto" ? (prefersDark() ? "dark" : "light") : settings.theme;
}

function resolvedMapStyle() {
  if (settings.mapStyle !== "auto") return settings.mapStyle;
  return resolvedTheme() === "dark" ? "dark" : "liberty";
}

function formatSpeed(kmh) {
  const unit = UNITS[settings.units] || UNITS.kmh;
  const value = unit.fromKmh(kmh);
  return {
    value: value < 10 ? value.toFixed(1) : String(Math.round(value)),
    label: t(unit.label),
  };
}

// -------------------------------------------------------------------- state

const state = {
  profiles: [],
  profile: null,
  server: null,
  draft: [],          // route being drawn, as [lat, lon]
  loopMode: "once",
  favourites: [],
  savedRoutes: [],
  lastRoute: null,     // the last route actually started, for "repeat"
  speedOverride: null, // km/h set by the slider, null = profile default
};

// ---------------------------------------------------------------------- api

async function api(path, body) {
  const options = body === undefined
    ? {}
    : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const response = await fetch(path, options);
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    throw new Error((payload && payload.detail) || `${response.status} ${response.statusText}`);
  }
  return payload;
}

function toast(message, kind = "", ms = 5200) {
  const node = document.createElement("div");
  node.className = `toast ${kind}`;
  node.textContent = message;
  $("toasts").append(node);
  setTimeout(() => {
    node.classList.add("leaving");
    setTimeout(() => node.remove(), 320);
  }, ms);
}

async function call(path, body, okMessage) {
  try {
    const result = await api(path, body === undefined ? {} : body);
    if (okMessage) toast(okMessage, "good", 2600);
    if (result && result.state) applyState(result.state);
    else if (result && "connected" in result) applyState(result);
    return result;
  } catch (error) {
    toast(error.message, "bad", 9000);
    return null;
  }
}

// ---------------------------------------------------------------------- map
//
// Two engines behind one interface. Raster maps run on Leaflet, which draws
// plain <img> tiles and works anywhere. Vector maps run on MapLibre, which is
// sharper and themeable but needs WebGL and a working animation loop -- not a
// given on every machine. The choice is a setting, "Auto" picks a vector map
// where WebGL is available and falls back to raster otherwise, and a vector map
// that fails to draw demotes itself to raster rather than showing a black box.

let engine = null;        // the active map engine
let engineType = null;    // which MAP_TYPES key it is rendering
let puckMoving = false;
const anim = { from: null, to: null, start: 0, duration: POLL_MS, heading: 0, moving: false };

const MAP_TYPES = {
  osm: {
    kind: "raster",
    label: "Standard",
    url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    maxZoom: 19,
    dimInDark: true,  // a light basemap, inverted by CSS under the dark theme
  },
  satellite: {
    kind: "raster",
    label: "Satellite",
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    attribution: "Esri, Maxar, Earthstar Geographics",
    maxZoom: 19,
  },
  topo: {
    kind: "raster",
    label: "Topographic",
    url: "https://tile.opentopomap.org/{z}/{x}/{y}.png",
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>, SRTM | '
      + '&copy; <a href="https://opentopomap.org/">OpenTopoMap</a> (CC-BY-SA)',
    maxZoom: 17,
    dimInDark: true,
  },
  vector: { kind: "vector", label: "Vector", style: "/static/map-styles/liberty.json" },
  vectorLight: { kind: "vector", label: "Vector light", style: "/static/map-styles/positron.json" },
  vectorDark: { kind: "vector", label: "Vector dark", style: "/static/map-styles/dark.json" },
};

//: Above this many points a route is a recorded track, not something edited by
//: hand, so the waypoints stay a cheap layer instead of a marker each.
const MAX_DRAGGABLE_WAYPOINTS = 60;

//: A journey leg is drawn in the colour of how it is travelled, so where the
//: driving ends and the flight begins is obvious at a glance.
const LEG_COLOURS = {
  stand: "#30d158", walk: "#30d158", run: "#30d158",
  bike: "#32d7c3",
  city: "#0a84ff", highway: "#0a84ff",
  train: "#ff9f0a",
  plane: "#bf5af2",
};

const LEG_COLOUR_DEFAULT = "#5e5ce6";

const LINE_STYLES = {
  route: { color: "#5e5ce6", width: 5, opacity: 0.55 },
  trail: { color: "#30d158", width: 4, opacity: 0.9 },
  draft: { color: "#0a84ff", width: 4, opacity: 0.85, dashed: true },
};

function metres(a, b) {
  const R = 6371008.8;
  const toRad = Math.PI / 180;
  const dLat = (b[0] - a[0]) * toRad;
  const dLon = (b[1] - a[1]) * toRad;
  const h = Math.sin(dLat / 2) ** 2
    + Math.cos(a[0] * toRad) * Math.cos(b[0] * toRad) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
}

//: Set to false once we learn that animation frames never fire here. Map
//: animations depend on them, so they are skipped rather than left half-done.
let framesWork = true;

/** Whether this browser can run a vector map at all. */
function webglAvailable() {
  try {
    const probe = document.createElement("canvas");
    return Boolean(probe.getContext("webgl2") || probe.getContext("webgl"));
  } catch {
    return false;
  }
}

function resolvedMapType() {
  const chosen = settings.mapStyle;
  if (chosen && chosen !== "auto" && MAP_TYPES[chosen]) return chosen;
  if (!webglAvailable()) return "osm";
  return resolvedTheme() === "dark" ? "vectorDark" : "vector";
}

/** Load a script or stylesheet once, resolving when the browser has it. */
const loaded = new Map();

function ensureAsset(url, tag) {
  if (loaded.has(url)) return loaded.get(url);
  const promise = new Promise((resolve, reject) => {
    const element = document.createElement(tag);
    if (tag === "script") {
      element.src = url;
      element.async = false;
    } else {
      element.rel = "stylesheet";
      element.href = url;
    }
    element.onload = () => resolve();
    element.onerror = () => reject(new Error(`could not load ${url}`));
    document.head.append(element);
  });
  loaded.set(url, promise);
  return promise;
}

/** Width the side panel takes from the map, so fitted shapes stay visible. */
function panelOffsetX() {
  const panel = $("panel");
  if (!panel || panel.classList.contains("collapsed")) return 20;
  const width = panel.getBoundingClientRect().width;
  // On a narrow screen the panel sits at the bottom, not the side.
  return window.innerWidth > 900 ? Math.min(width + 24, window.innerWidth * 0.45) : 20;
}

function puckElement() {
  const element = document.createElement("div");
  element.className = "puck";
  element.innerHTML = '<div class="puck-cone"></div><div class="puck-halo"></div><div class="puck-dot"></div>';
  return element;
}

// ------------------------------------------------------------ raster engine

class LeafletEngine {
  constructor(type) {
    this.kind = "raster";
    this.type = type;
    this.lines = {};
    this.marker = null;
    this.waypointLayer = null;
  }

  async init(container, { center, zoom, onClick, onUserMove, onContextMenu }) {
    await ensureAsset("/static/vendor/leaflet.css", "link");
    await ensureAsset("/static/vendor/leaflet.js", "script");
    const spec = MAP_TYPES[this.type];

    this.map = L.map(container, { zoomControl: true, worldCopyJump: true })
      .setView([center[0], center[1]], zoom);
    this.tiles = L.tileLayer(spec.url, {
      attribution: spec.attribution,
      maxZoom: spec.maxZoom || 19,
      crossOrigin: true,
    }).addTo(this.map);
    document.body.classList.toggle("map-dimmable", Boolean(spec.dimInDark));

    for (const [id, style] of Object.entries(LINE_STYLES)) {
      this.lines[id] = L.polyline([], {
        color: style.color,
        weight: style.width,
        opacity: style.opacity,
        lineCap: "round",
        dashArray: style.dashed ? "9 7" : undefined,
      }).addTo(this.map);
    }
    this.waypointLayer = L.layerGroup().addTo(this.map);
    this.map.on("click", (event) => onClick(event.latlng.lat, event.latlng.lng, event.originalEvent));
    // Dragging and wheel-zooming are the user taking over; programmatic moves
    // (setView from camera following) raise neither.
    this.map.on("dragstart", onUserMove);
    this.map.getContainer().addEventListener("wheel", onUserMove, { passive: true });
    this.map.on("contextmenu", (event) => {
      onContextMenu(event.latlng.lat, event.latlng.lng,
        event.originalEvent.clientX, event.originalEvent.clientY);
    });

    // Tiles are plain images here, so a failure is a network problem.
    this.tiles.on("tileerror", () => warnTilesOnce());
    return new Promise((resolve) => this.map.whenReady(() => resolve()));
  }

  setLine(id, points) {
    if (this.lines[id]) this.lines[id].setLatLngs(points);
  }

  setLegs(legs) {
    if (!this.legLayer) this.legLayer = L.layerGroup().addTo(this.map);
    this.legLayer.clearLayers();
    for (const leg of legs) {
      L.polyline(leg.points.map((p) => [p.lat, p.lon]), {
        color: LEG_COLOURS[leg.profile] || LEG_COLOUR_DEFAULT,
        weight: leg.profile === "plane" ? 3 : 5,
        // A finished leg fades back; the one being travelled stays bright.
        opacity: leg.done ? 0.35 : 0.85,
        dashArray: leg.profile === "plane" ? "10 8" : undefined,
        lineCap: "round",
      }).addTo(this.legLayer);
    }
  }

  setWaypoints(points, handlers = {}) {
    this.waypointLayer.clearLayers();
    const interactive = Boolean(handlers.onMove) && points.length <= MAX_DRAGGABLE_WAYPOINTS;
    points.forEach((point, index) => {
      const marker = L.marker(point, {
        interactive,
        draggable: interactive,
        keyboard: false,
        title: interactive ? t("Drag to move · click to delete") : "",
        icon: L.divIcon({ className: "waypoint-label", html: String(index + 1), iconAnchor: [11, 11] }),
      }).addTo(this.waypointLayer);
      if (!interactive) return;
      marker.on("dragend", () => {
        const position = marker.getLatLng();
        handlers.onMove(index, position.lat, position.lng);
      });
      marker.on("click", (event) => {
        L.DomEvent.stopPropagation(event);
        if (handlers.onRemove) handlers.onRemove(index);
      });
    });
  }

  setMarker(lat, lon) {
    if (!this.marker) {
      const element = puckElement();
      this.marker = L.marker([lat, lon], {
        draggable: true,
        keyboard: false,
        zIndexOffset: 900,
        icon: L.divIcon({ className: "", html: element.outerHTML, iconSize: [26, 26], iconAnchor: [13, 13] }),
      }).addTo(this.map);
      this.marker.on("dragend", () => {
        const position = this.marker.getLatLng();
        if (this.onPuckDrag) this.onPuckDrag(position.lat, position.lng);
      });
    } else if (!this.marker.dragging || !this.marker.dragging.moving()) {
      this.marker.setLatLng([lat, lon]);
    }
    return this.marker.getElement();
  }

  setPuckDragHandler(handler) { this.onPuckDrag = handler; }

  easeTo(lat, lon, zoom) {
    this.map.setView([lat, lon], zoom === undefined ? this.map.getZoom() : Math.max(this.map.getZoom(), zoom), {
      animate: framesWork, duration: 0.6,
    });
  }

  fitBounds(points) {
    try {
      this.map.fitBounds(L.latLngBounds(points), {
        paddingTopLeft: [panelOffsetX(), 110],
        paddingBottomRight: [60, 60],
      });
    } catch (error) {
      console.warn("fitBounds failed, centring instead:", error);
      this.map.fitBounds(L.latLngBounds(points));
    }
  }

  getCenter() {
    const centre = this.map.getCenter();
    return [centre.lat, centre.lng];
  }

  isInView(lat, lon) {
    return this.map.getBounds().pad(-0.22).contains([lat, lon]);
  }

  setPitchEnabled() { /* raster maps are flat */ }

  destroy() {
    this.map.remove();
    this.marker = null;
  }
}

// ------------------------------------------------------------ vector engine

class MapLibreEngine {
  constructor(type) {
    this.kind = "vector";
    this.type = type;
    this.ready = false;
    this.marker = null;
    this.pending = { route: [], trail: [], draft: [], waypoints: [] };
  }

  async init(container, { center, zoom, onClick, onUserMove, onContextMenu }) {
    await ensureAsset("/static/vendor/maplibre-gl.css", "link");
    await ensureAsset("/static/vendor/maplibre-gl.js", "script");

    this.map = new maplibregl.Map({
      container,
      style: MAP_TYPES[this.type].style,
      center: [center[1], center[0]],
      zoom,
      attributionControl: { compact: true },
      pitchWithRotate: settings.allowPitch,
      dragRotate: settings.allowPitch,
    });
    document.body.classList.remove("map-dimmable");
    this.map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");
    this.map.addControl(new maplibregl.ScaleControl({ maxWidth: 110, unit: "metric" }), "bottom-left");
    if (!settings.allowPitch) this.map.touchZoomRotate.disableRotation();

    this.map.on("click", (event) => onClick(event.lngLat.lat, event.lngLat.lng, event.originalEvent));
    // originalEvent is only present when a real gesture caused the move, which
    // is what separates the user panning from the camera following.
    this.map.on("dragstart", (event) => { if (event.originalEvent) onUserMove(); });
    this.map.on("zoomstart", (event) => { if (event.originalEvent) onUserMove(); });
    this.map.on("contextmenu", (event) => {
      onContextMenu(event.lngLat.lat, event.lngLat.lng,
        event.originalEvent.clientX, event.originalEvent.clientY);
    });
    this.map.on("error", (event) => {
      const message = String((event && event.error && event.error.message) || "");
      if (/tile|fetch|network|load/i.test(message)) warnTilesOnce();
    });

    // Layers belong to the style, so they are (re)created on every style load.
    this.map.on("style.load", () => {
      this._installLayers();
      this.ready = true;
      for (const [id, points] of Object.entries(this.pending)) {
        if (id === "waypoints") this.setWaypoints(points);
        else this.setLine(id, points);
      }
      if (this.pendingLegs) this.setLegs(this.pendingLegs);
    });

    // A vector map that never finishes loading is the failure this whole
    // two-engine arrangement exists for; surface it instead of hanging.
    await new Promise((resolve, reject) => {
      const timer = setTimeout(
        () => reject(new Error("the map never rendered (no WebGL, or animation frames never fire)")),
        10000,
      );
      this.map.on("load", () => { clearTimeout(timer); resolve(); });
      this.map.on("error", (event) => {
        const message = String((event && event.error && event.error.message) || "");
        if (/webgl|context|style/i.test(message)) {
          clearTimeout(timer);
          reject(new Error(message));
        }
      });
    });
  }

  _installLayers() {
    const empty = { type: "FeatureCollection", features: [] };
    for (const id of ["trail", "route", "draft", "waypoints", "legs"]) {
      this.map.addSource(id, { type: "geojson", data: empty });
    }
    this.map.addLayer({
      id: "journey-legs",
      type: "line",
      source: "legs",
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": ["get", "colour"],
        "line-width": ["get", "width"],
        "line-opacity": ["get", "opacity"],
      },
    });
    for (const id of ["route", "trail", "draft"]) {
      const style = LINE_STYLES[id];
      this.map.addLayer({
        id: `${id}-line`,
        type: "line",
        source: id,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": style.color,
          "line-width": style.width,
          "line-opacity": style.opacity,
          ...(style.dashed ? { "line-dasharray": [2, 1.6] } : {}),
        },
      });
    }
    this.map.addLayer({
      id: "waypoint-dots",
      type: "circle",
      source: "waypoints",
      paint: {
        "circle-radius": 9,
        "circle-color": "#0a84ff",
        "circle-stroke-width": 2,
        "circle-stroke-color": "#ffffff",
      },
    });
    this.map.addLayer({
      id: "waypoint-labels",
      type: "symbol",
      source: "waypoints",
      layout: {
        "text-field": ["get", "label"],
        "text-size": 11,
        "text-font": this._font(),
        "text-allow-overlap": true,
      },
      paint: { "text-color": "#ffffff" },
    });
  }

  /** Styles ship different fonts; borrow one the loaded style already uses. */
  _font() {
    for (const layer of this.map.getStyle().layers || []) {
      const font = layer.layout && layer.layout["text-font"];
      if (Array.isArray(font) && typeof font[0] === "string") return font;
    }
    return ["Noto Sans Regular"];
  }

  _set(id, data) {
    const source = this.ready && this.map.getSource(id);
    if (source) source.setData(data);
  }

  setLegs(legs) {
    this.pendingLegs = legs;
    this._set("legs", {
      type: "FeatureCollection",
      features: legs.map((leg) => ({
        type: "Feature",
        geometry: {
          type: "LineString",
          coordinates: leg.points.map((p) => [p.lon, p.lat]),
        },
        properties: {
          colour: LEG_COLOURS[leg.profile] || LEG_COLOUR_DEFAULT,
          width: leg.profile === "plane" ? 3 : 5,
          opacity: leg.done ? 0.35 : 0.85,
        },
      })),
    });
  }

  setLine(id, points) {
    this.pending[id] = points;
    this._set(id, points.length < 2 ? { type: "FeatureCollection", features: [] } : {
      type: "FeatureCollection",
      features: [{
        type: "Feature",
        geometry: { type: "LineString", coordinates: points.map(([lat, lon]) => [lon, lat]) },
        properties: {},
      }],
    });
  }

  setWaypoints(points, handlers = {}) {
    this.pending.waypoints = points;
    this.pending.waypointHandlers = handlers;
    const interactive = Boolean(handlers.onMove) && points.length <= MAX_DRAGGABLE_WAYPOINTS;

    for (const marker of this.waypointMarkers || []) marker.remove();
    this.waypointMarkers = [];

    if (!interactive) {
      this._set("waypoints", {
        type: "FeatureCollection",
        features: points.map(([lat, lon], index) => ({
          type: "Feature",
          geometry: { type: "Point", coordinates: [lon, lat] },
          properties: { label: String(index + 1) },
        })),
      });
      return;
    }

    // Draggable markers replace the layer, so the layer is emptied first.
    this._set("waypoints", { type: "FeatureCollection", features: [] });
    points.forEach(([lat, lon], index) => {
      const element = document.createElement("div");
      element.className = "waypoint-label";
      element.textContent = String(index + 1);
      element.title = t("Drag to move · click to delete");
      const marker = new maplibregl.Marker({ element, draggable: true })
        .setLngLat([lon, lat])
        .addTo(this.map);
      marker.on("dragend", () => {
        const position = marker.getLngLat();
        handlers.onMove(index, position.lat, position.lng);
      });
      let dragged = false;
      marker.on("dragstart", () => { dragged = true; });
      element.addEventListener("click", (event) => {
        event.stopPropagation();
        if (dragged) { dragged = false; return; }
        if (handlers.onRemove) handlers.onRemove(index);
      });
      this.waypointMarkers.push(marker);
    });
  }

  setMarker(lat, lon) {
    if (!this.marker) {
      this.marker = new maplibregl.Marker({ element: puckElement(), draggable: true })
        .setLngLat([lon, lat])
        .addTo(this.map);
      this.marker.on("dragstart", () => { this._puckDragging = true; });
      this.marker.on("dragend", () => {
        this._puckDragging = false;
        const position = this.marker.getLngLat();
        if (this.onPuckDrag) this.onPuckDrag(position.lat, position.lng);
      });
    } else if (!this._puckDragging) {
      this.marker.setLngLat([lon, lat]);
    }
    return this.marker.getElement();
  }

  setPuckDragHandler(handler) { this.onPuckDrag = handler; }

  easeTo(lat, lon, zoom) {
    this.map.easeTo({
      center: [lon, lat],
      zoom: zoom === undefined ? this.map.getZoom() : Math.max(this.map.getZoom(), zoom),
      duration: framesWork ? 600 : 0,
    });
  }

  fitBounds(points) {
    const bounds = points.reduce(
      (acc, [lat, lon]) => acc.extend([lon, lat]),
      new maplibregl.LngLatBounds([points[0][1], points[0][0]], [points[0][1], points[0][0]]),
    );
    // A padding *object* makes cameraForBounds return undefined in this
    // MapLibre build; a plain number works, and the offset keeps the shape
    // clear of the side panel.
    try {
      this.map.fitBounds(bounds, {
        padding: 70,
        offset: [panelOffsetX() / 2, 0],
        duration: framesWork ? 800 : 0,
      });
    } catch (error) {
      console.warn("fitBounds failed, centring instead:", error);
      const centre = bounds.getCenter();
      this.map.easeTo({ center: centre, duration: 0 });
    }
  }

  getCenter() {
    const centre = this.map.getCenter();
    return [centre.lat, centre.lng];
  }

  isInView(lat, lon) {
    return this.map.getBounds().contains([lon, lat]);
  }

  setPitchEnabled(enabled) {
    if (enabled) {
      this.map.dragRotate.enable();
      this.map.touchZoomRotate.enableRotation();
    } else {
      this.map.dragRotate.disable();
      this.map.touchZoomRotate.disableRotation();
      this.map.easeTo({ pitch: 0, bearing: 0, duration: 400 });
    }
  }

  destroy() {
    for (const marker of this.waypointMarkers || []) marker.remove();
    this.waypointMarkers = [];
    this.map.remove();
    this.marker = null;
  }
}

// -------------------------------------------------------------- map plumbing

let tilesWarned = false;

function warnTilesOnce() {
  if (tilesWarned) return;
  tilesWarned = true;
  toast(t("Map tiles are not loading — looks like there is no internet. Coordinates, "
          + "routes and GPX work as usual."), "", 7000);
}

//: True while the user has taken the map over by hand. Camera following stays
//: off until they ask to go back, the way a navigation app behaves -- otherwise
//: the view snaps back to the device every poll and the map cannot be browsed.
let followSuspended = false;

function updateRecenterButton() {
  const button = $("recenter-btn");
  if (!button) return;
  button.hidden = !(followSuspended && anim.to);
}

function suspendFollow() {
  if (followSuspended) return;
  followSuspended = true;
  updateRecenterButton();
}

function resumeFollow() {
  followSuspended = false;
  updateRecenterButton();
  if (engine && anim.to) engine.easeTo(anim.to[0], anim.to[1]);
}

function onMapClick(lat, lon, event) {
  // No drawing mode to toggle: a plain click moves the device, Shift-click
  // (or the right-click menu) builds a route.
  if (event && event.shiftKey) addDraftPoint([lat, lon]);
  else call("/api/teleport", { lat, lon });
}

// ----------------------------------------------------------- context menu

let menuPoint = null;

function showMapMenu(lat, lon, x, y) {
  menuPoint = [lat, lon];
  const menu = $("map-menu");
  $("menu-coords").textContent = `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
  menu.hidden = false;
  // Keep it on screen when the click lands near an edge.
  const box = menu.getBoundingClientRect();
  menu.style.left = `${Math.min(x, window.innerWidth - box.width - 12)}px`;
  menu.style.top = `${Math.min(y, window.innerHeight - box.height - 12)}px`;
}

function hideMapMenu() {
  $("map-menu").hidden = true;
  menuPoint = null;
}

function wireMapMenu() {
  $("menu-teleport").addEventListener("click", () => {
    if (menuPoint) call("/api/teleport", { lat: menuPoint[0], lon: menuPoint[1] });
    hideMapMenu();
  });
  $("menu-route").addEventListener("click", () => {
    if (menuPoint) addDraftPoint(menuPoint);
    hideMapMenu();
  });
  $("menu-save").addEventListener("click", () => {
    if (menuPoint) addFavouriteAt(menuPoint[0], menuPoint[1]);
    hideMapMenu();
  });
  $("menu-copy").addEventListener("click", async () => {
    if (!menuPoint) return;
    const text = `${menuPoint[0].toFixed(6)}, ${menuPoint[1].toFixed(6)}`;
    try {
      await navigator.clipboard.writeText(text);
      toast(t("Copied: {text}", { text }), "good", 2600);
    } catch {
      toast(text, "", 6000);
    }
    hideMapMenu();
  });
  document.addEventListener("click", (event) => {
    if (!$("map-menu").contains(event.target)) hideMapMenu();
  });
  window.addEventListener("keydown", (event) => { if (event.key === "Escape") hideMapMenu(); });
}

async function buildEngine(type, view) {
  const Engine = MAP_TYPES[type].kind === "vector" ? MapLibreEngine : LeafletEngine;
  const instance = new Engine(type);
  await instance.init("map", {
    center: view.center,
    zoom: view.zoom,
    onClick: onMapClick,
    onUserMove: suspendFollow,
    onContextMenu: showMapMenu,
  });
  // Dragging the puck is the most direct way to say "be here instead".
  instance.setPuckDragHandler((lat, lon) => call("/api/teleport", { lat, lon }));
  return instance;
}

function currentView() {
  if (!engine) return { center: [55.751244, 37.618423], zoom: 11 };
  const zoom = engine.map && engine.map.getZoom ? engine.map.getZoom() : 11;
  return { center: engine.getCenter(), zoom };
}

let switching = false;

async function applyMapType(requested) {
  const type = requested || resolvedMapType();
  if (engine && engineType === type) return;
  if (switching) return;
  switching = true;

  // The old engine has to go first: both Leaflet and MapLibre refuse to
  // initialise into a container that still holds a live map.
  const view = currentView();
  if (engine) {
    engine.destroy();
    engine = null;
    engineType = null;
  }
  anim.to = null;  // the marker belonged to the engine just torn down

  try {
    engine = await buildEngine(type, view);
  } catch (error) {
    console.warn(`map "${type}" unavailable:`, error);
    if (MAP_TYPES[type].kind === "vector") {
      toast(t("The vector map did not start ({error}). Switching to Standard — you can "
              + "pick it again in settings.", { error: error.message }), "bad", 9000);
      settings.mapStyle = "osm";
      saveSettings();
      renderSettings();
    } else {
      toast(t("Map \"{name}\" did not start: {error}",
              { name: t(MAP_TYPES[type].label), error: error.message }), "bad", 9000);
    }
    try {
      engine = await buildEngine("osm", view);
    } catch (fallbackError) {
      switching = false;
      toast(t("Could not show the map: {error}", { error: fallbackError.message }), "bad", 12000);
      return;
    }
  }

  engineType = engine.type;
  followSuspended = false;
  updateRecenterButton();
  document.body.classList.toggle("map-vector", engine.kind === "vector");
  switching = false;
  redrawAll();
}

async function initMap() {
  // Diagnostics handle, opt-in via ?debug=1 -- the module scope is otherwise
  // unreachable from the browser console.
  if (new URLSearchParams(location.search).has("debug")) {
    window.__iosloc = {
      get engine() { return engine; },
      get engineType() { return engineType; },
      settings,
      state,
      MAP_TYPES,
      webglAvailable,
      anim,
      get followSuspended() { return followSuspended; },
      // Lets a test drive the puck without a device attached.
      simulatePosition: (lat, lon) => updatePuck({ lat, lon, heading: 0, speed_kmh: 5 }, true),
    };
  }
  await applyMapType();
  startPuckAnimation();
}

function setLine(id, points) {
  if (engine) engine.setLine(id, points);
}

function redrawAll() {
  renderDraft();
  const server = state.server;
  if (!server || !engine) return;
  const route = server.route;
  if (engine && engine.setLegs) engine.setLegs(journey && journey.shape ? journey.shape : []);
  setLine("route", route && route.points ? route.points.map((p) => [p.lat, p.lon]) : []);
  setLine("trail", settings.showTrail ? (server.trail || []).map((p) => [p.lat, p.lon]) : []);
  if (server.position) updatePuck(server.position, puckMoving);
}

/** Drive the puck animation, falling back to a timer where rAF never fires.
 *
 * Some embedded webviews keep a document "visible" yet never run animation
 * frames; the puck would then freeze between polls. A timer at ~30 Hz keeps it
 * moving there, and is only installed when real frames never arrive.
 */
function startPuckAnimation() {
  let frames = 0;
  const frame = (now) => { frames += 1; requestAnimationFrame(frame); animatePuck(now); };
  requestAnimationFrame(frame);

  setTimeout(() => {
    if (frames > 0) return;
    framesWork = false;
    console.warn("requestAnimationFrame is not firing; falling back to a timer");
    setInterval(() => animatePuck(performance.now()), 33);
  }, 1200);
}

/** Interpolate the puck between the last two reported fixes. */
function animatePuck(now) {
  if (!anim.to || !engine) return;

  let position = anim.to;
  if (anim.from) {
    const t = Math.min(1, (now - anim.start) / anim.duration);
    position = [
      anim.from[0] + (anim.to[0] - anim.from[0]) * t,
      anim.from[1] + (anim.to[1] - anim.from[1]) * t,
    ];
  }
  const element = engine.setMarker(position[0], position[1]);
  if (!element) return;
  const puck = element.classList.contains("puck") ? element : element.querySelector(".puck");
  if (puck) puck.classList.toggle("still", !anim.moving);
  const cone = element.querySelector(".puck-cone");
  if (cone) cone.style.transform = `translate(-50%, -100%) rotate(${anim.heading}deg)`;
}

function updatePuck(position, moving) {
  const next = [position.lat, position.lon];
  const jumped = anim.to ? metres(anim.to, next) > MAX_SMOOTH_JUMP_M : true;
  anim.from = jumped ? null : anim.to;
  anim.to = next;
  anim.start = performance.now();
  anim.heading = position.heading || 0;
  anim.moving = Boolean(moving);
  puckMoving = Boolean(moving);

  updateRecenterButton();
  if (!settings.followCamera || followSuspended || !engine) return;
  if (jumped || !engine.isInView(next[0], next[1])) {
    engine.easeTo(next[0], next[1]);
  }
}

function fitToPoints(points) {
  if (engine && points.length) engine.fitBounds(points);
}

// ------------------------------------------------------------------- draft

function addDraftPoint(point) {
  state.draft.push(point);
  renderDraft();
}

function renderDraft() {
  setLine("draft", state.draft);
  if (engine) {
    engine.setWaypoints(state.draft, {
      onMove: (index, lat, lon) => {
        state.draft[index] = [lat, lon];
        renderDraft();
      },
      onRemove: (index) => {
        state.draft.splice(index, 1);
        renderDraft();
      },
    });
  }

  let text = t("no points");
  if (state.draft.length === 1) text = t("1 point");
  else if (state.draft.length > 1) {
    let total = 0;
    for (let i = 1; i < state.draft.length; i += 1) total += metres(state.draft[i - 1], state.draft[i]);
    text = `${t("{n} points", { n: state.draft.length })} · ${formatDistance(total)}`;
  }
  $("route-stat").textContent = text;
  $("go-btn").disabled = state.draft.length < 1 || !(state.server && state.server.connected);
  $("gpx-export-btn").disabled = state.draft.length < 2;
  $("route-save-btn").disabled = state.draft.length < 2 && !(plannedLegs && plannedLegs.length);
  // The editing controls are noise until a route exists.
  $("route-actions").hidden = state.draft.length === 0;
  $("route-hint").hidden = state.draft.length > 0;
}


// --------------------------------------------------------------- profiles

function renderProfiles() {
  const host = $("profiles");
  host.innerHTML = "";
  for (const profile of state.profiles) {
    const button = document.createElement("button");
    button.type = "button";
    button.role = "radio";
    button.dataset.key = profile.key;
    button.title = t(profile.note || profile.label);
    button.innerHTML = `<svg viewBox="0 0 24 24">${ICONS[profile.icon] || ICONS.pin}</svg>`
      + `<span>${escapeHtml(t(profile.label))}</span>`;
    button.addEventListener("click", () => selectProfile(profile.key));
    host.append(button);
  }
  markProfile();
}

function markProfile() {
  const key = state.profile && state.profile.key;
  for (const button of $("profiles").children) button.classList.toggle("on", button.dataset.key === key);
  if (!state.profile) return;
  $("profile-note").textContent = t(state.profile.note || "");
  const slider = $("speed-slider");
  slider.max = String(Math.max(20, Math.ceil(state.profile.speed_kmh * 2)));
  slider.value = String(Math.round(state.speedOverride ?? state.profile.speed_kmh));
  showSpeed(Number(slider.value));
}

async function selectProfile(key) {
  const profile = state.profiles.find((item) => item.key === key);
  if (!profile) return;
  state.profile = profile;
  state.speedOverride = null;
  markProfile();
  // Always tell the server, even with no device attached: it owns the current
  // profile, and the next poll would otherwise overwrite this choice with the
  // server's own value a few hundred milliseconds later.
  await call("/api/profile", { profile: key });
}

function showSpeed(kmh) {
  const { value, label } = formatSpeed(kmh);
  $("speed-value").textContent = `${value} ${label}`;
}

// ------------------------------------------------------------------ state

function applyState(server) {
  state.server = server;
  if (!$("verify-backdrop").hidden) renderVerify();
  const device = server.device;

  $("device-line").textContent = device
    ? `${device.name} · ${device.model || device.product_type} · iOS ${device.ios_version}`
    : t("No device connected");

  const pill = $("transport-pill");
  if (server.connected && server.transport) {
    pill.hidden = false;
    pill.textContent = server.transport === "lockdown" ? "USB · lockdown" : `USB · ${server.transport}`;
  } else {
    pill.hidden = true;
  }

  $("connect-btn").textContent = t(server.connected ? "Disconnect" : "Connect");
  $("connect-btn").classList.toggle("btn-primary", !server.connected);
  $("connect-btn").classList.toggle("btn-ghost", server.connected);

  if (server.profile && (!state.profile || state.profile.key !== server.profile.key)) {
    state.profile = state.profiles.find((item) => item.key === server.profile.key) || server.profile;
    markProfile();
  }

  const moving = server.mode === "route" || server.mode === "free";
  const active = Boolean(server.position);
  $("pause-btn").disabled = !moving && !server.paused;
  $("pause-btn").textContent = t(server.paused ? "Resume" : "Pause");
  $("stop-btn").disabled = !active;
  $("restore-btn").disabled = !server.override_active;
  $("go-btn").disabled = state.draft.length < 1 || !server.connected;

  const position = server.position;
  const speed = formatSpeed(position ? position.speed_kmh : 0);
  $("hud-speed").textContent = speed.value;
  $("hud-unit").textContent = speed.label;
  $("hud-coords").textContent = position
    ? `${position.lat.toFixed(5)}, ${position.lon.toFixed(5)}`
    : "—";
  $("hud-mode").textContent = server.paused
    ? t("paused")
    : t({ route: "route", free: "manual", hold: "holding", idle: "idle" }[server.mode] || server.mode);
  const needle = $("hud-needle");
  if (needle && position) needle.style.transform = `rotate(${position.heading}deg)`;

  // A journey reports which leg is running; a plain route does not.
  const journey = server.journey;
  $("journey-stat").textContent = journey
    ? t("leg {n} of {total}", { n: journey.leg, total: journey.legs })
    : "";
  if (journey && !journey.finished) {
    $("hud-mode").textContent = translateNote(journey.note) || t("route");
  }

  const route = server.route;
  const progressBar = $("hud-progress");
  if (route && typeof route.progress === "number") {
    progressBar.hidden = false;
    progressBar.firstElementChild.style.width = `${(route.progress * 100).toFixed(1)}%`;
  } else {
    progressBar.hidden = true;
  }

  const etaRow = $("hud-eta-row");
  if (route && route.eta_s) {
    etaRow.hidden = false;
    $("hud-eta").textContent = formatDuration(route.eta_s)
      + (route.remaining_m ? ` · ${formatDistance(route.remaining_m)}` : "");
  } else {
    etaRow.hidden = true;
  }

  setLine("route", route && route.points ? route.points.map((p) => [p.lat, p.lon]) : []);
  setLine("trail", settings.showTrail ? (server.trail || []).map((p) => [p.lat, p.lon]) : []);

  if (position) updatePuck(position, moving && !server.paused);

  if (server.error) {
    toast(server.error, "bad", 9000);
    state.server.error = null;
  }
}

function formatDuration(seconds) {
  seconds = Math.round(seconds);
  if (seconds < 60) return t("{n} s", { n: seconds });
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return t("{n} min", { n: minutes });
  return t("{h} h {m} min", { h: Math.floor(minutes / 60), m: minutes % 60 });
}

function formatDistance(metresValue) {
  return metresValue < 1000
    ? t("{n} m", { n: Math.round(metresValue) })
    : t("{n} km", { n: (metresValue / 1000).toFixed(metresValue < 10000 ? 2 : 1) });
}

// ------------------------------------------------------------- connection

/** Connect without asking when there is exactly one ready device.
 *
 * Picking from a list of one is pure ceremony. Anything else -- no devices, a
 * device that needs attention, or several of them -- opens the chooser, where
 * the reason is spelled out.
 */
async function autoConnect() {
  let devices = [];
  try {
    ({ devices } = await api("/api/devices"));
  } catch {
    await openDevicePicker();
    return;
  }

  const ready = devices.filter((device) => device.paired && !device.problem);
  if (ready.length === 1 && devices.length === 1) {
    toast(t("Connecting to {name}…", { name: ready[0].name }), "", 2600);
    const result = await call("/api/connect", { udid: ready[0].udid });
    if (result) {
      toast(t("Connected. Click the map to set a position."), "good", 4200);
      await poll();
      return;
    }
  }
  await openDevicePicker();
}

async function openDevicePicker() {
  $("sheet-backdrop").hidden = false;
  await refreshDevices();
}

async function refreshDevices() {
  const list = $("device-list");
  list.innerHTML = `<p class="muted">${t("Querying USB…")}</p>`;
  try {
    const { devices } = await api("/api/devices");
    if (!devices.length) {
      list.innerHTML = `<p class="muted">${escapeHtml(t(
        "Nothing found. Connect an iPhone by cable, unlock the screen and confirm "
        + "\"Trust This Computer\"."))}</p>`;
      return;
    }
    list.innerHTML = "";
    for (const device of devices) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "device";
      const bad = Boolean(device.problem);
      button.innerHTML = `
        <span class="device-dot ${bad ? "bad" : ""}"></span>
        <span class="device-info">
          <strong>${escapeHtml(device.name)}</strong>
          <small>${escapeHtml(device.model || device.product_type)} · iOS ${escapeHtml(device.ios_version)}
            · ${escapeHtml(device.connection_type)}</small>
          ${bad ? `<span class="device-warn">${escapeHtml(device.problem)}</span>` : ""}
        </span>`;
      button.addEventListener("click", () => connect(device.udid));
      list.append(button);
    }
  } catch (error) {
    list.innerHTML = `<p class="device-warn">${escapeHtml(error.message)}</p>`;
  }
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

async function connect(udid) {
  const button = $("connect-btn");
  button.disabled = true;
  button.textContent = t("Connecting…");
  const result = await call("/api/connect", {
    udid,
    enable_developer_mode: $("devmode-toggle").checked,
  });
  button.disabled = false;
  if (result) {
    $("sheet-backdrop").hidden = true;
    toast(t("Connected. Click the map to set a position."), "good", 4200);
  }
  await poll();
}

// -------------------------------------------------------------- free roam

const held = new Set();
let steerTimer = null;

const KEY_HEADINGS = {
  w: 0, ArrowUp: 0, s: 180, ArrowDown: 180,
  a: 270, ArrowLeft: 270, d: 90, ArrowRight: 90,
  ц: 0, ы: 180, ф: 270, в: 90, // same physical keys on a Russian layout
};

function headingFromKeys() {
  const vectors = [...held].map((key) => KEY_HEADINGS[key]).filter((value) => value !== undefined);
  if (!vectors.length) return null;
  let x = 0;
  let y = 0;
  for (const heading of vectors) {
    const radians = (heading * Math.PI) / 180;
    x += Math.sin(radians);
    y += Math.cos(radians);
  }
  if (Math.abs(x) < 1e-9 && Math.abs(y) < 1e-9) return null;
  return ((Math.atan2(x, y) * 180) / Math.PI + 360) % 360;
}

function pushSteer(heading, speedKmh) {
  // Coalesce bursts of key events into one request.
  if (steerTimer) clearTimeout(steerTimer);
  steerTimer = setTimeout(() => {
    steerTimer = null;
    call("/api/steer", { heading, speed_kmh: speedKmh });
  }, 90);
}

function currentSpeed(boost) {
  const base = state.speedOverride ?? (state.profile ? state.profile.speed_kmh : 5);
  return boost ? base * 2.5 : base;
}

function onKeyDown(event) {
  if (!settings.keyboardControl) return;
  if (isTypingTarget(event)) return;
  if (!(event.key in KEY_HEADINGS)) return;
  event.preventDefault();
  held.add(event.key);
  const heading = headingFromKeys();
  if (heading === null) return;
  markDpad(heading);
  pushSteer(heading, currentSpeed(event.shiftKey));
}

function onKeyUp(event) {
  if (!settings.keyboardControl) return;
  if (!(event.key in KEY_HEADINGS)) return;
  held.delete(event.key);
  const heading = headingFromKeys();
  markDpad(heading);
  if (heading === null) pushSteer(undefined, 0);
  else pushSteer(heading, currentSpeed(event.shiftKey));
}

/** True when the key event came from a text field, where typing wins.
 *
 * `event.target` is not always an Element (it is `window` for a programmatic
 * dispatch), and calling `.matches` on one of those throws, taking the whole
 * handler with it.
 */
function isTypingTarget(event) {
  const target = event.target;
  return target instanceof Element && target.matches("input, textarea, select, [contenteditable]");
}

/** Digits 1-8 switch profile, space pauses -- both outside text fields. */
function onShortcut(event) {
  if (isTypingTarget(event)) return;
  if (event.ctrlKey || event.altKey || event.metaKey) return;
  if (!$("settings-backdrop").hidden || !$("sheet-backdrop").hidden) return;

  if (event.code === "Space") {
    event.preventDefault();
    const paused = state.server && state.server.paused;
    call(paused ? "/api/resume" : "/api/pause", {});
    return;
  }
  const digit = Number(event.key);
  if (Number.isInteger(digit) && digit >= 1 && digit <= state.profiles.length) {
    event.preventDefault();
    selectProfile(state.profiles[digit - 1].key);
  }
}

function markDpad(heading) {
  for (const button of $("dpad").children) {
    const value = button.dataset.heading;
    button.classList.toggle("held", value !== undefined && heading !== null && Number(value) === Math.round(heading));
  }
}

// ------------------------------------------------------------------- misc

function parseCoordinates(text) {
  const match = text.trim().match(/^(-?\d+(?:[.,]\d+)?)\s*[,; ]\s*(-?\d+(?:[.,]\d+)?)$/);
  if (!match) return null;
  const lat = Number(match[1].replace(",", "."));
  const lon = Number(match[2].replace(",", "."));
  if (Number.isNaN(lat) || Number.isNaN(lon)) return null;
  if (Math.abs(lat) > 90 || Math.abs(lon) > 180) return null;
  return { lat, lon };
}

async function goTo(lat, lon, zoom) {
  if (engine) engine.easeTo(lat, lon, zoom || 14);
  await call("/api/teleport", { lat, lon });
}

async function runSearch() {
  const query = $("search-input").value.trim();
  if (!query) return;
  const results = $("search-results");

  const direct = parseCoordinates(query);
  if (direct) {
    results.hidden = true;
    await goTo(direct.lat, direct.lon, 15);
    return;
  }

  if (!settings.online) {
    toast(t("That is not a pair of coordinates. Enable Online services in settings to "
            + "search by address."), "bad");
    return;
  }

  results.hidden = false;
  results.innerHTML = `<p class="muted" style="padding:8px 11px">${t("Searching…")}</p>`;
  try {
    const { results: found } = await api("/api/search", { query, online: true });
    if (!found.length) {
      results.innerHTML = `<p class="muted" style="padding:8px 11px">${t("Nothing found")}</p>`;
      return;
    }
    results.innerHTML = "";
    for (const item of found) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = item.name;
      button.addEventListener("click", async () => {
        results.hidden = true;
        await goTo(item.lat, item.lon, 14);
      });
      results.append(button);
    }
  } catch (error) {
    results.innerHTML = `<p class="device-warn" style="padding:8px 11px">${escapeHtml(error.message)}</p>`;
  }
}

async function snapToRoads() {
  if (state.draft.length < 2) {
    toast(t("Need at least two route points."), "bad");
    return;
  }
  if (!settings.online) {
    toast(t("Road routing runs on the public OSRM server. Enable Online services "
            + "in settings."), "bad");
    return;
  }
  const profileKey = state.profile ? state.profile.key : "walk";
  const mode = ["walk", "run", "stand"].includes(profileKey)
    ? "walking"
    : profileKey === "bike" ? "cycling" : "driving";
  try {
    const result = await api("/api/snap", {
      points: state.draft.map(([lat, lon]) => ({ lat, lon })),
      mode,
      online: true,
    });
    state.draft = result.points.map((p) => [p.lat, p.lon]);
    renderDraft();
    toast(t("Road route: {distance}", { distance: formatDistance(result.distance_m) }),
      "good", 3600);
  } catch (error) {
    toast(error.message, "bad", 9000);
  }
}

function loadGpx(file) {
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const xml = new DOMParser().parseFromString(String(reader.result), "application/xml");
      if (xml.querySelector("parsererror")) throw new Error(t("the file does not parse as GPX"));
      let nodes = [...xml.getElementsByTagName("trkpt")];
      if (!nodes.length) nodes = [...xml.getElementsByTagName("rtept")];
      if (!nodes.length) nodes = [...xml.getElementsByTagName("wpt")];
      const points = nodes
        .map((node) => [Number(node.getAttribute("lat")), Number(node.getAttribute("lon"))])
        .filter(([lat, lon]) => Number.isFinite(lat) && Number.isFinite(lon));
      if (!points.length) throw new Error(t("no points in the file"));
      state.draft = points;
      renderDraft();
      fitToPoints(points);
      toast(t("Loaded {count} points. Press Start moving.", { count: points.length }),
        "good", 4200);
    } catch (error) {
      toast(t("GPX: {error}", { error: error.message }), "bad");
    }
  };
  reader.onerror = () => toast(t("Could not read the file"), "bad");
  reader.readAsText(file);
}

function exportGpx() {
  if (state.draft.length < 2) {
    toast(t("Need at least two route points first."), "bad");
    return;
  }
  const points = state.draft
    .map(([lat, lon]) => `      <trkpt lat="${lat.toFixed(7)}" lon="${lon.toFixed(7)}"/>`)
    .join("\n");
  const gpx = `<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="ios-loc" xmlns="http://www.topografix.com/GPX/1/1">
  <trk>
    <name>ios-loc route</name>
    <trkseg>
${points}
    </trkseg>
  </trk>
</gpx>
`;
  const url = URL.createObjectURL(new Blob([gpx], { type: "application/gpx+xml" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `ios-loc-route-${new Date().toISOString().slice(0, 10)}.gpx`;
  link.click();
  URL.revokeObjectURL(url);
  toast(t("Route exported to GPX."), "good", 3000);
}

// ----------------------------------------------------------- saved routes

const ROUTES_KEY = "iosloc.routes";
const LAST_ROUTE_KEY = "iosloc.lastRoute";

function readStored(key, fallback) {
  try {
    return JSON.parse(localStorage.getItem(key) || "null") ?? fallback;
  } catch {
    return fallback;
  }
}

function writeStored(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* private mode or blocked storage: it just will not persist */
  }
}

function loadSavedRoutes() {
  state.savedRoutes = readStored(ROUTES_KEY, []);
  state.lastRoute = readStored(LAST_ROUTE_KEY, null);
  renderSavedRoutes();
}

function routeLength(points) {
  let total = 0;
  for (let i = 1; i < points.length; i += 1) total += metres(points[i - 1], points[i]);
  return total;
}

function saveCurrentJourney() {
  if (!plannedLegs || !plannedLegs.length) return false;
  const name = (prompt(t("Journey name:"), t("Journey")) || "").trim();
  if (!name) return true;
  state.savedRoutes.unshift({
    name,
    legs: plannedLegs.map((leg) => ({
      points: leg.points.map((p) => [Number(p.lat.toFixed(7)), Number(p.lon.toFixed(7))]),
      profile: leg.profile,
      note: leg.note,
    })),
  });
  state.savedRoutes = state.savedRoutes.slice(0, 25);
  writeStored(ROUTES_KEY, state.savedRoutes);
  renderSavedRoutes();
  toast(t("Journey saved as \"{name}\".", { name }), "good", 3000);
  return true;
}

function saveCurrentRoute() {
  // A planned journey is saved whole, legs and all, rather than flattened.
  if (saveCurrentJourney()) return;
  if (state.draft.length < 2) {
    toast(t("Need at least two route points first."), "bad");
    return;
  }
  const suggested = `${t("Route")} ${formatDistance(routeLength(state.draft))}`;
  const name = (prompt(t("Route name:"), suggested) || "").trim();
  if (!name) return;
  state.savedRoutes.unshift({
    name,
    points: state.draft.map(([lat, lon]) => [Number(lat.toFixed(7)), Number(lon.toFixed(7))]),
    profile: state.profile ? state.profile.key : null,
    mode: state.loopMode,
  });
  state.savedRoutes = state.savedRoutes.slice(0, 25);
  writeStored(ROUTES_KEY, state.savedRoutes);
  renderSavedRoutes();
  toast(t("Route saved as \"{name}\".", { name }), "good", 3000);
}

function loadSavedRoute(item) {
  if (item.legs) {
    plannedLegs = item.legs.map((leg) => ({
      points: leg.points.map(([lat, lon]) => ({ lat, lon })),
      profile: leg.profile,
      note: leg.note,
    }));
    const box = $("journey-plan");
    box.hidden = false;
    box.innerHTML = plannedLegs.map((leg) => `
      <div class="leg">
        <span class="leg-note">${escapeHtml(translateNote(leg.note))}</span>
        <span class="leg-meta">${escapeHtml(t(profileLabel(leg.profile)))}</span>
      </div>`).join("")
      + `<button class="btn btn-accent btn-wide" id="journey-go">${escapeHtml(t("Start the journey"))}</button>`;
    $("journey-go").addEventListener("click", startPlannedJourney);
    fitToPoints(plannedLegs.flatMap((leg) => leg.points.map((p) => [p.lat, p.lon])));
    toast(t("Loaded \"{name}\". Press Start the journey.", { name: item.name }), "good", 3600);
    return;
  }
  state.draft = item.points.map(([lat, lon]) => [lat, lon]);
  if (item.mode) {
    state.loopMode = item.mode;
    for (const button of $("loop-mode").children) {
      button.classList.toggle("on", button.dataset.mode === item.mode);
    }
  }
  if (item.profile && state.profiles.some((p) => p.key === item.profile)) selectProfile(item.profile);
  renderDraft();
  fitToPoints(state.draft);
  toast(t("Loaded \"{name}\". Press Start moving.", { name: item.name }), "good", 3600);
}

function renderSavedRoutes() {
  const host = $("saved-routes");
  host.innerHTML = "";
  if (!state.savedRoutes.length) {
    host.innerHTML = `<p class="muted" style="font-size:12.5px;margin:0">${t("Empty for now")}</p>`;
    return;
  }
  state.savedRoutes.forEach((item, index) => {
    const row = document.createElement("div");
    row.className = "fav";
    const length = formatDistance(item.legs
      ? item.legs.reduce((sum, leg) => sum + routeLength(leg.points), 0)
      : routeLength(item.points.map(([lat, lon]) => [lat, lon])));
    row.innerHTML = `<button class="fav-go" type="button">${escapeHtml(item.name)}`
      + ` <span class="muted">· ${length}</span></button>`
      + `<button class="fav-del" type="button" aria-label="${t("Delete")}">&times;</button>`;
    row.querySelector(".fav-go").addEventListener("click", () => loadSavedRoute(item));
    row.querySelector(".fav-del").addEventListener("click", () => {
      state.savedRoutes.splice(index, 1);
      writeStored(ROUTES_KEY, state.savedRoutes);
      renderSavedRoutes();
    });
    host.append(row);
  });
}

function repeatLastRoute() {
  if (!state.lastRoute || !state.lastRoute.points || state.lastRoute.points.length < 1) {
    toast(t("No route has been started yet."), "bad");
    return;
  }
  loadSavedRoute({ ...state.lastRoute, name: t("Repeat last") });
}

// ----------------------------------------------------------------- verifying

let verifyTimer = null;

/** Show, live, whether fixes are actually reaching the device. */
function openVerify() {
  $("verify-backdrop").hidden = false;
  renderVerify();
  if (verifyTimer) clearInterval(verifyTimer);
  verifyTimer = setInterval(renderVerify, 1000);
}

function closeVerify() {
  $("verify-backdrop").hidden = true;
  if (verifyTimer) { clearInterval(verifyTimer); verifyTimer = null; }
}

function verifyRow(key, value, tone) {
  return `<span class="k">${escapeHtml(key)}</span>`
    + `<span class="v ${tone || ""}">${escapeHtml(value)}</span>`;
}

function renderVerify() {
  const server = state.server;
  const box = $("verify-rows");
  if (!server) {
    box.innerHTML = verifyRow(t("Connection"), t("no answer"), "bad");
    return;
  }

  const rows = [];
  rows.push(server.connected
    ? verifyRow(t("Device"), server.device ? server.device.name : t("connected"), "good")
    : verifyRow(t("Device"), t("not connected"), "bad"));

  if (server.connected) {
    rows.push(verifyRow(t("Channel"), server.transport || "lockdown", "good"));
  }

  rows.push(server.override_active
    ? verifyRow(t("Override"), t("on"), "good")
    : verifyRow(t("Override"), t("off — place a point on the map"), "wait"));

  rows.push(verifyRow(t("Fixes accepted"), String(server.fixes_sent || 0),
    server.fixes_sent ? "good" : "wait"));

  // The counter alone cannot tell a live stream from a stalled one; the age can.
  const age = server.last_fix_age;
  if (age === null || age === undefined) {
    rows.push(verifyRow(t("Last fix"), t("none yet"), "wait"));
  } else if (age <= 25) {
    rows.push(verifyRow(t("Last fix"), t("{n} s ago", { n: age.toFixed(1) }), "good"));
  } else {
    rows.push(verifyRow(t("Last fix"), t("{n} s ago", { n: age.toFixed(0) }), "bad"));
  }

  box.innerHTML = rows.join("");

  const position = server.position;
  $("verify-point").textContent = position
    ? `${position.lat.toFixed(5)}, ${position.lon.toFixed(5)}`
    : t("— no point set yet");
}

// ------------------------------------------------------------- activity log

let logTimer = null;

/** Show the log, and keep it live while the dialog is open. */
async function openLog() {
  $("log-backdrop").hidden = false;
  await refreshLog();
  // Polling only runs while the dialog is visible, so a closed log costs nothing.
  if (logTimer) clearInterval(logTimer);
  logTimer = setInterval(refreshLog, 2000);
}

function closeLog() {
  $("log-backdrop").hidden = true;
  if (logTimer) { clearInterval(logTimer); logTimer = null; }
}

function formatUptime(seconds) {
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h) return t("up {h} h {m} min", { h, m });
  if (m) return t("up {m} min", { m });
  return t("up {s} s", { s });
}

/** Colour a line by its level, so a problem stands out in a wall of text. */
function logLineClass(line) {
  if (/\b(ERROR|CRITICAL|Traceback)\b/.test(line)) return "log-line-err";
  if (/\bWARNING\b/.test(line)) return "log-line-warn";
  return "";
}

async function refreshLog() {
  const view = $("log-view");
  const dot = $("log-dot");
  try {
    const data = await api("/api/log?lines=400");
    dot.classList.remove("bad");
    $("log-state").textContent = t("Running");
    $("log-uptime").textContent = data.uptime_s === undefined
      ? ""
      : `${formatUptime(data.uptime_s)} · PID ${data.pid}`;

    if (!data.available) {
      view.textContent = t("The log file has not been created yet.");
      $("log-path").textContent = "";
      return;
    }

    // Keep the view pinned to the end unless the user scrolled up to read.
    const follow = $("log-follow").checked;
    view.innerHTML = data.lines
      .map((line) => {
        const cls = logLineClass(line);
        const text = escapeHtml(line);
        return cls ? `<span class="${cls}">${text}</span>` : text;
      })
      .join("\n");
    $("log-path").textContent = t("File: {path} ({size} KB)", {
      path: data.path, size: data.size_kb,
    });
    if (follow) view.scrollTop = view.scrollHeight;
  } catch (error) {
    // A failed request is itself the answer: the program is no longer serving.
    dot.classList.add("bad");
    $("log-state").textContent = t("Not responding");
    $("log-uptime").textContent = escapeHtml(error.message);
  }
}

async function copyLog() {
  try {
    await navigator.clipboard.writeText($("log-view").textContent);
    toast(t("Log copied."), "good", 2200);
  } catch (error) {
    toast(t("Could not copy: {error}", { error: error.message }), "bad");
  }
}

// ------------------------------------------------------------------- presets

const CUSTOM_PRESETS_KEY = "iosloc.presets";

function loadCustomPresets() {
  return readStored(CUSTOM_PRESETS_KEY, []);
}

function presetName(preset) {
  // Built-in presets carry both languages; ones you add carry a plain string.
  if (typeof preset.name === "string") return preset.name;
  return preset.name[getLanguage()] || preset.name.en || preset.id;
}

async function openPresets() {
  $("presets-backdrop").hidden = false;
  const list = $("presets-list");
  list.innerHTML = `<p class="muted">${t("Checking\u2026")}</p>`;
  try {
    const { presets } = await api("/api/presets");
    renderPresets(presets, loadCustomPresets());
  } catch (error) {
    list.innerHTML = `<p class="device-warn">${escapeHtml(error.message)}</p>`;
  }
}

function renderPresets(builtIn, custom) {
  const list = $("presets-list");
  list.innerHTML = "";

  const addRow = (preset, removable) => {
    const row = document.createElement("div");
    row.className = "fav";
    row.innerHTML = `<button class="fav-go" type="button">${escapeHtml(presetName(preset))}`
      + ` <span class="muted">\u00b7 ${escapeHtml(preset.stops.join(" \u2192 "))}</span></button>`
      + (removable ? `<button class="fav-del" type="button" aria-label="${t("Delete")}">&times;</button>` : "");
    row.querySelector(".fav-go").addEventListener("click", () => usePreset(preset));
    if (removable) {
      row.querySelector(".fav-del").addEventListener("click", () => {
        const kept = loadCustomPresets().filter((item) => item.id !== preset.id);
        writeStored(CUSTOM_PRESETS_KEY, kept);
        renderPresets(builtIn, kept);
      });
    }
    list.append(row);
  };

  if (custom.length) {
    const heading = document.createElement("p");
    heading.className = "muted";
    heading.style.cssText = "margin:4px 0 2px;font-size:12.5px";
    heading.textContent = t("Your routes");
    list.append(heading);
    custom.forEach((preset) => addRow(preset, true));
  }
  builtIn.forEach((preset) => addRow(preset, false));
}

/** Plan a preset from wherever the device is now. */
async function usePreset(preset) {
  $("presets-backdrop").hidden = true;
  const position = state.server && state.server.position;
  const from = position
    ? { lat: position.lat, lon: position.lon }
    : (engine ? { lat: engine.getCenter()[0], lon: engine.getCenter()[1] } : null);

  const box = $("journey-plan");
  box.hidden = false;
  box.innerHTML = `<p class="muted">${t("Planning\u2026")}</p>`;
  try {
    const plan = await api("/api/plan", { stops: preset.stops, start: from });
    showPlan(plan);
  } catch (error) {
    box.innerHTML = `<p class="device-warn">${escapeHtml(error.message)}</p>`;
  }
}

async function addCustomPreset() {
  const raw = $("preset-stops").value.trim();
  const stops = raw.split(/[\s,;]+/).filter(Boolean).map((code) => code.toUpperCase());
  if (stops.length < 2) {
    toast(t("Need at least two airports."), "bad");
    return;
  }
  // Check every code before saving, so a saved route always works.
  for (const code of stops) {
    const { airports } = await api(`/api/airports?q=${encodeURIComponent(code)}`);
    if (!airports.some((a) => a.iata === code || a.icao === code)) {
      toast(t("Unknown airport code: {code}", { code }), "bad", 6000);
      return;
    }
  }

  const custom = loadCustomPresets();
  custom.unshift({ id: `custom-${Date.now()}`, name: stops.join(" \u2192 "), stops });
  writeStored(CUSTOM_PRESETS_KEY, custom.slice(0, 30));
  $("preset-stops").value = "";
  toast(t("Route added."), "good", 2600);
  const { presets } = await api("/api/presets");
  renderPresets(presets, loadCustomPresets());
}

// ---------------------------------------------------------------- journey

let plannedLegs = null;

/** Resolve what the user typed into a destination: airport, coordinates or address. */
async function resolveDestination(query) {
  const direct = parseCoordinates(query);
  if (direct) return [{ label: `${direct.lat.toFixed(4)}, ${direct.lon.toFixed(4)}`, ...direct }];

  // Airports come from the bundled database, so this works with no internet.
  const { airports } = await api(`/api/airports?q=${encodeURIComponent(query)}`);
  const results = airports.map((a) => ({ label: a.label, lat: a.lat, lon: a.lon, airport: true }));
  if (results.length || !settings.online) return results;

  const { results: places } = await api("/api/search", { query, online: true });
  return places.map((p) => ({ label: p.name, lat: p.lat, lon: p.lon }));
}

async function planJourneyTo(destination) {
  const position = state.server && state.server.position;
  const from = position
    ? { lat: position.lat, lon: position.lon }
    : (engine ? { lat: engine.getCenter()[0], lon: engine.getCenter()[1] } : null);
  if (!from) {
    toast(t("Set a starting position on the map first."), "bad");
    return;
  }

  const box = $("journey-plan");
  box.hidden = false;
  box.innerHTML = `<p class="muted">${t("Planning…")}</p>`;

  try {
    const plan = await api("/api/plan", {
      start: from,
      finish: { lat: destination.lat, lon: destination.lon },
    });
    showPlan(plan);
  } catch (error) {
    box.innerHTML = `<p class="device-warn">${escapeHtml(error.message)}</p>`;
  }
}

/** Translate a leg note like "Drive to SVO" without losing the code in it. */
function translateNote(note) {
  if (!note) return "";
  const patterns = [
    [/^Drive to (\w+)$/, "Drive to {code}"],
    [/^Drive from (\w+)$/, "Drive from {code}"],
    [/^Walk from (\w+)$/, "Walk from {code}"],
  ];
  for (const [regex, key] of patterns) {
    const match = note.match(regex);
    if (match) return t(key, { code: match[1] });
  }
  const flight = note.match(/^Fly (\w+) → (\w+)$/);
  if (flight) return t("Fly {from} → {to}", { from: flight[1], to: flight[2] });
  return t(note);
}

/** Render a planned journey and offer to start it. */
function showPlan(plan) {
  plannedLegs = plan.legs;
  const box = $("journey-plan");
  box.hidden = false;
  box.innerHTML = plan.legs.map((leg) => `
      <div class="leg" style="border-left-color:${LEG_COLOURS[leg.profile] || LEG_COLOUR_DEFAULT}">
        <span class="leg-note">${escapeHtml(translateNote(leg.note))}</span>
        <span class="leg-meta">${escapeHtml(t(profileLabel(leg.profile)))} \u00b7 ${formatDistance(leg.length_m)}</span>
      </div>`).join("")
    + `<p class="muted journey-total">${escapeHtml(t("Total: {distance}", { distance: formatDistance(plan.total_m) }))}</p>`
    + `<button class="btn btn-accent btn-wide" id="journey-go">${escapeHtml(t("Start the journey"))}</button>`;
  $("journey-go").addEventListener("click", startPlannedJourney);
  renderDraft();  // a plan counts as something worth saving
  // Show the whole trip, so the flight legs are obvious.
  fitToPoints(plan.legs.flatMap((leg) => leg.points.map((p) => [p.lat, p.lon])));
}

function profileLabel(key) {
  const profile = state.profiles.find((p) => p.key === key);
  return profile ? profile.label : key;
}

async function startPlannedJourney() {
  if (!plannedLegs) return;
  const result = await call("/api/journey", { legs: plannedLegs, from_current: true });
  if (result && result.journey) {
    const { total_m: total, eta_s: eta } = result.journey;
    toast(t("On the way: {distance}", { distance: formatDistance(total) })
      + (eta ? t(", about {time}", { time: formatDuration(eta) }) : ""), "good", 5000);
    $("journey-plan").hidden = true;
    $("dest-results").hidden = true;
    plannedLegs = null;
    renderDraft();
  }
}

async function runDestinationSearch() {
  const query = $("dest-input").value.trim();
  if (!query) return;
  const box = $("dest-results");
  box.hidden = false;
  box.innerHTML = `<p class="muted" style="padding:8px 11px">${t("Searching…")}</p>`;
  try {
    const found = await resolveDestination(query);
    if (!found.length) {
      box.innerHTML = `<p class="muted" style="padding:8px 11px">${t("Nothing found")}</p>`;
      return;
    }
    if (found.length === 1) {
      box.hidden = true;
      await planJourneyTo(found[0]);
      return;
    }
    box.innerHTML = "";
    for (const item of found) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = item.label;
      button.addEventListener("click", async () => {
        box.hidden = true;
        await planJourneyTo(item);
      });
      box.append(button);
    }
  } catch (error) {
    box.innerHTML = `<p class="device-warn" style="padding:8px 11px">${escapeHtml(error.message)}</p>`;
  }
}

// ------------------------------------------------------------- favourites

function loadFavourites() {
  try {
    state.favourites = JSON.parse(localStorage.getItem("iosloc.favs") || "[]");
  } catch {
    state.favourites = [];
  }
  renderFavourites();
}

function saveFavourites() {
  try {
    localStorage.setItem("iosloc.favs", JSON.stringify(state.favourites));
  } catch {
    /* private mode or blocked storage: favourites just do not persist */
  }
}

function renderFavourites() {
  const host = $("favs");
  host.innerHTML = "";
  if (!state.favourites.length) {
    host.innerHTML = `<p class="muted" style="font-size:12.5px;margin:0">${t("Empty for now")}</p>`;
    return;
  }
  state.favourites.forEach((item, index) => {
    const row = document.createElement("div");
    row.className = "fav";
    row.innerHTML = `<button class="fav-go" type="button">${escapeHtml(item.name)}</button>`
      + `<button class="fav-del" type="button" aria-label="${t("Delete")}">&times;</button>`;
    row.querySelector(".fav-go").addEventListener("click", () => goTo(item.lat, item.lon, 15));
    row.querySelector(".fav-del").addEventListener("click", () => {
      state.favourites.splice(index, 1);
      saveFavourites();
      renderFavourites();
    });
    host.append(row);
  });
}

function addFavouriteAt(lat, lon) {
  const name = (prompt(t("Place name:"), `${lat.toFixed(4)}, ${lon.toFixed(4)}`) || "").trim();
  if (!name) return;
  state.favourites.unshift({ name, lat, lon });
  state.favourites = state.favourites.slice(0, 40);
  saveFavourites();
  renderFavourites();
  toast(t("Place saved as \"{name}\".", { name }), "good", 2600);
}

function addFavourite() {
  const position = state.server && state.server.position;
  const centre = engine ? engine.getCenter() : [0, 0];
  addFavouriteAt(position ? position.lat : centre[0], position ? position.lon : centre[1]);
}

// --------------------------------------------------------------- settings UI

function applyTheme() {
  document.documentElement.dataset.theme = resolvedTheme();
}

function markSegmented(id, value) {
  for (const button of $(id).children) button.classList.toggle("on", button.dataset.value === value);
}

function renderSettings() {
  markSegmented("set-lang", settings.language);
  markSegmented("set-theme", settings.theme);
  markSegmented("set-mapstyle", settings.mapStyle);
  markSegmented("set-units", settings.units);
  $("set-follow").checked = settings.followCamera;
  $("set-trail").checked = settings.showTrail;
  $("set-keys").checked = settings.keyboardControl;
  $("set-pitch").checked = settings.allowPitch;
  $("set-online").checked = settings.online;
  $("keys-hint").hidden = !settings.keyboardControl;
}

function wireSettings() {
  const segments = {
    "set-lang": (value) => {
      settings.language = value;
      applyLanguage();
    },
    "set-theme": (value) => {
      settings.theme = value;
      applyTheme();
      if (settings.mapStyle === "auto") applyMapType();
    },
    "set-mapstyle": (value) => {
      settings.mapStyle = value;
      applyMapType(value === "auto" ? null : value);
    },
    "set-units": (value) => {
      settings.units = value;
      markProfile();
      if (state.server) applyState(state.server);
    },
  };

  for (const [id, onChange] of Object.entries(segments)) {
    for (const button of $(id).children) {
      button.addEventListener("click", () => {
        markSegmented(id, button.dataset.value);
        onChange(button.dataset.value);
        saveSettings();
      });
    }
  }

  const toggles = {
    "set-follow": "followCamera",
    "set-trail": "showTrail",
    "set-keys": "keyboardControl",
    "set-pitch": "allowPitch",
    "set-online": "online",
  };
  for (const [id, key] of Object.entries(toggles)) {
    $(id).addEventListener("change", (event) => {
      settings[key] = event.target.checked;
      saveSettings();
      if (key === "showTrail") redrawAll();
      if (key === "keyboardControl") {
        held.clear();
        markDpad(null);
        $("keys-hint").hidden = !settings.keyboardControl;
      }
      if (key === "allowPitch" && engine) engine.setPitchEnabled(settings.allowPitch);
    });
  }

  $("settings-btn").addEventListener("click", () => {
    renderSettings();
    $("settings-backdrop").hidden = false;
  });
  $("settings-close").addEventListener("click", () => { $("settings-backdrop").hidden = true; });
  $("settings-backdrop").addEventListener("click", (event) => {
    if (event.target === $("settings-backdrop")) $("settings-backdrop").hidden = true;
  });
  $("settings-reset").addEventListener("click", () => {
    Object.assign(settings, DEFAULT_SETTINGS);
    saveSettings();
    applyTheme();
    applyLanguage();
    applyMapType();
    renderSettings();
    markProfile();
    toast(t("Settings reset."), "good", 2600);
  });

  // Follow the system theme live while "auto" is selected.
  if (window.matchMedia) {
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
      if (settings.theme !== "auto") return;
      applyTheme();
      if (settings.mapStyle === "auto") applyMapType();
    });
  }
}

// -------------------------------------------------------- phone access

async function showPhoneLink() {
  const body = $("phone-body");
  $("phone-backdrop").hidden = false;
  body.innerHTML = `<p class="muted">${t("Checking…")}</p>`;
  try {
    const info = await api("/api/phone-link");
    if (!info.enabled) {
      body.innerHTML = `<p class="muted">${escapeHtml(info.hint)}</p>`
        + `<p class="muted">${escapeHtml(t("The phone must be on the same Wi-Fi as the "
          + "computer. The cable is still needed — the override goes through it."))}</p>`;
      return;
    }
    const url = info.urls[0] || "";
    body.innerHTML = `
      <p>${escapeHtml(t("Scan the code with your phone, or open the link in its browser."))}</p>
      <img class="qr" src="/api/phone-qr.png" alt="${escapeHtml(t("QR code for the phone"))}">
      <code class="phone-url">${escapeHtml(url)}</code>
      ${info.urls.length > 1
        ? `<p class="muted">${escapeHtml(t("Other addresses of this computer:"))} ${
            info.urls.slice(1).map((u) => escapeHtml(u)).join("<br>")}</p>`
        : ""}
      <p class="muted">${escapeHtml(t("The access code lasts while the program runs. The "
        + "phone must be on the same Wi-Fi; the cable is still needed, the override goes "
        + "through it."))}</p>`;
  } catch (error) {
    body.innerHTML = `<p class="device-warn">${escapeHtml(error.message)}</p>`;
  }
}

// ------------------------------------------------------------------- wire

function wire() {
  $("connect-btn").addEventListener("click", async () => {
    if (state.server && state.server.connected) {
      await call("/api/disconnect", {}, t("Disconnected, real location restored"));
    } else {
      await openDevicePicker();
    }
  });

  $("sheet-close").addEventListener("click", () => { $("sheet-backdrop").hidden = true; });
  $("sheet-refresh").addEventListener("click", refreshDevices);

  $("devmode-reveal").addEventListener("click", async (event) => {
    // iOS keeps the Developer Mode switch hidden until a development tool has
    // talked to the device; this asks it to show up.
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const { message } = await api("/api/reveal-devmode", {});
      toast(message, "good", 16000);
    } catch (error) {
      toast(error.message, "bad", 10000);
    } finally {
      button.disabled = false;
    }
  });

  $("sheet-backdrop").addEventListener("click", (event) => {
    if (event.target === $("sheet-backdrop")) $("sheet-backdrop").hidden = true;
  });

  $("search-btn").addEventListener("click", runSearch);
  $("search-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); runSearch(); }
  });

  $("speed-slider").addEventListener("input", (event) => {
    const kmh = Number(event.target.value);
    state.speedOverride = kmh;
    showSpeed(kmh);
  });
  $("speed-slider").addEventListener("change", (event) => {
    if (state.server && state.server.connected) {
      call("/api/speed", { speed_kmh: Number(event.target.value) });
    }
  });

  for (const button of $("loop-mode").children) {
    button.addEventListener("click", () => {
      state.loopMode = button.dataset.mode;
      for (const sibling of $("loop-mode").children) sibling.classList.toggle("on", sibling === button);
    });
  }

  $("route-undo-btn").addEventListener("click", () => { state.draft.pop(); renderDraft(); });
  $("route-clear-btn").addEventListener("click", () => {
    state.draft = [];
    plannedLegs = null;
    $("journey-plan").hidden = true;
    renderDraft();
  });
  $("snap-btn").addEventListener("click", snapToRoads);
  $("gpx-export-btn").addEventListener("click", exportGpx);
  $("gpx-input").addEventListener("change", (event) => {
    const file = event.target.files && event.target.files[0];
    if (file) loadGpx(file);
    event.target.value = "";
  });

  $("go-btn").addEventListener("click", async () => {
    if (!state.draft.length) return;
    const result = await call("/api/route", {
      points: state.draft.map(([lat, lon]) => ({ lat, lon })),
      profile: state.profile ? state.profile.key : undefined,
      mode: state.loopMode,
      speed_kmh: state.speedOverride ?? undefined,
      from_current: Boolean(state.server && state.server.position),
    });
    if (result && result.route) {
      state.lastRoute = {
        points: state.draft.map(([lat, lon]) => [lat, lon]),
        profile: state.profile ? state.profile.key : null,
        mode: state.loopMode,
      };
      writeStored(LAST_ROUTE_KEY, state.lastRoute);
      const { length_m: length, speed_kmh: speed, eta_s: eta } = result.route;
      const shown = formatSpeed(speed);
      toast(t("On the way: {distance} at {speed}",
              { distance: formatDistance(length), speed: `${shown.value} ${shown.label}` })
        + (eta ? t(", about {time}", { time: formatDuration(eta) }) : ""), "good", 4200);
    }
  });

  $("pause-btn").addEventListener("click", () => {
    const paused = state.server && state.server.paused;
    call(paused ? "/api/resume" : "/api/pause", {});
  });
  $("stop-btn").addEventListener("click", () => call("/api/stop", { restore: false }));
  $("restore-btn").addEventListener("click", () =>
    call("/api/stop", { restore: true }, t("Real location restored")));

  for (const button of $("dpad").children) {
    if (button.id === "dpad-stop") {
      button.addEventListener("click", () => call("/api/steer", { speed_kmh: 0 }));
      continue;
    }
    button.addEventListener("click", () => {
      const heading = Number(button.dataset.heading);
      markDpad(heading);
      call("/api/steer", { heading, speed_kmh: currentSpeed(false) });
    });
  }

  $("recenter-btn").addEventListener("click", resumeFollow);

  $("phone-btn").addEventListener("click", showPhoneLink);
  $("phone-close").addEventListener("click", () => { $("phone-backdrop").hidden = true; });
  $("phone-backdrop").addEventListener("click", (event) => {
    if (event.target === $("phone-backdrop")) $("phone-backdrop").hidden = true;
  });
  $("verify-btn").addEventListener("click", openVerify);
  $("verify-close").addEventListener("click", closeVerify);
  $("verify-backdrop").addEventListener("click", (event) => {
    if (event.target === $("verify-backdrop")) closeVerify();
  });

  $("log-btn").addEventListener("click", openLog);
  $("log-close").addEventListener("click", closeLog);
  $("log-copy").addEventListener("click", copyLog);
  $("log-backdrop").addEventListener("click", (event) => {
    if (event.target === $("log-backdrop")) closeLog();
  });

  $("presets-btn").addEventListener("click", openPresets);
  $("presets-close").addEventListener("click", () => { $("presets-backdrop").hidden = true; });
  $("presets-backdrop").addEventListener("click", (event) => {
    if (event.target === $("presets-backdrop")) $("presets-backdrop").hidden = true;
  });
  $("preset-add").addEventListener("click", addCustomPreset);
  $("preset-stops").addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); addCustomPreset(); }
  });

  $("dest-btn").addEventListener("click", runDestinationSearch);
  $("dest-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); runDestinationSearch(); }
  });

  $("fav-add-btn").addEventListener("click", addFavourite);
  $("route-save-btn").addEventListener("click", saveCurrentRoute);
  $("route-repeat-btn").addEventListener("click", repeatLastRoute);

  $("minimize-btn").addEventListener("click", async () => {
    try {
      const { hidden } = await api("/api/minimize", {});
      toast(t(hidden
        ? "Window minimised. The program keeps running — its icon is in the tray, by the clock."
        : "Nothing to minimise: there is no console window. The tray icon is still there."),
        "good", 5000);
    } catch (error) {
      toast(error.message, "bad");
    }
  });

  const collapse = () => {
    $("panel").classList.toggle("collapsed");
    document.body.classList.toggle("panel-hidden", $("panel").classList.contains("collapsed"));
  };
  $("panel-grip").addEventListener("click", collapse);
  $("panel-grip").addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); collapse(); }
  });
  $("reveal-panel").addEventListener("click", collapse);

  window.addEventListener("keydown", onShortcut);
  window.addEventListener("keydown", onKeyDown);
  window.addEventListener("keyup", onKeyUp);
  window.addEventListener("blur", () => { held.clear(); markDpad(null); });

  document.addEventListener("dragover", (event) => event.preventDefault());
  document.addEventListener("drop", (event) => {
    event.preventDefault();
    const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
    if (file && /\.gpx$/i.test(file.name)) loadGpx(file);
  });

  // Esc closes whichever dialog is open.
  window.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    $("settings-backdrop").hidden = true;
    $("sheet-backdrop").hidden = true;
    $("presets-backdrop").hidden = true;
    closeLog();
    closeVerify();
  });
}

// ------------------------------------------------------------------- boot

async function poll() {
  try {
    applyState(await api("/api/state"));
  } catch {
    /* the server restarted or is still binding; the next tick retries */
  }
}

async function boot() {
  loadSettings();
  applyTheme();
  setLanguage(resolvedLanguage());
  applyTranslations();

  await initMap();
  wire();
  wireSettings();
  wireMapMenu();
  renderSettings();
  loadFavourites();
  loadSavedRoutes();
  renderDraft();

  try {
    const { profiles } = await api("/api/profiles");
    state.profiles = profiles;
    state.profile = profiles.find((item) => item.key === "walk") || profiles[0];
    renderProfiles();
  } catch (error) {
    toast(t("Could not load profiles: {error}", { error: error.message }), "bad");
  }

  await poll();
  setInterval(poll, POLL_MS);

  if (!(state.server && state.server.connected)) {
    await autoConnect();
  }
}

boot();
