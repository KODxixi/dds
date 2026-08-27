const COLORS = Object.freeze({
  site: "#c87961",
  competitors: "#78acaf",
  resources: "#91aa96",
  risks: "#c86d60",
});

const SITE_TEXTURE = "https://a.amap.com/Loca/static/loca-v2/demos/images/breath_red.png";

function featureCollection(features = []) {
  return { type: "FeatureCollection", features };
}

function pointOf(descriptor) {
  const source = descriptor?.amapPosition || descriptor?.position || descriptor;
  const lng = Number(source?.lng ?? source?.lon ?? source?.longitude);
  const lat = Number(source?.lat ?? source?.latitude);
  if (!Number.isFinite(lng) || !Number.isFinite(lat)) return null;
  if (lng < -180 || lng > 180 || lat < -90 || lat > 90) return null;
  return [lng, lat];
}

function pointFeature(descriptor, overrides = {}) {
  const coordinates = pointOf(descriptor);
  if (!coordinates) return null;
  const data = descriptor.data || {};
  return {
    type: "Feature",
    geometry: { type: "Point", coordinates },
    properties: {
      id: String(descriptor.id),
      label: descriptor.label || descriptor.id,
      layer: descriptor.layer,
      color: descriptor.color || COLORS[descriptor.layer],
      distanceKm: Number(data.distance_km ?? data.distanceKm) || null,
      unitPriceCny: Number(data.unit_price_cny ?? data.unitPriceCny ?? data.price) || null,
      category: data.category || data.property_type || descriptor.source || null,
      severity: data.severity || data.level || null,
      ...overrides,
    },
  };
}

function lineFeature(id, coordinates, properties = {}) {
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates },
    properties: { id, ...properties },
  };
}

function markerIcon(color, shape = "circle") {
  const body = shape === "diamond"
    ? `<path d="M16 3 29 16 16 29 3 16Z" fill="${color}" fill-opacity=".88" stroke="#F2F4EF" stroke-opacity=".72" stroke-width="2"/>`
    : `<circle cx="16" cy="16" r="11" fill="${color}" fill-opacity=".88" stroke="#F2F4EF" stroke-opacity=".72" stroke-width="2"/>`;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32">${body}</svg>`;
  return `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`;
}

function priceOf(feature) {
  return Number(feature?.properties?.unitPriceCny) || 1;
}

export class LocaDataScene {
  constructor({ map, reducedMotion = false, onFeature = null } = {}) {
    if (!map) throw new TypeError("LocaDataScene requires an AMap instance");
    this.map = map;
    this.reducedMotion = reducedMotion;
    this.onFeature = onFeature;
    this.loca = null;
    this.layers = new Map();
    this.sources = new Set();
    this.listeners = [];
    this.layerState = {};
    this.highlightedIds = new Set();
    this.competitorIds = new Set();
    this.quality = "balanced";
    this.mode = "3d";
  }

  initialize() {
    const Loca = globalThis.Loca;
    if (!Loca?.Container) throw new Error("Loca 2.0 runtime is unavailable");
    if (this.loca) return this;
    this.loca = new Loca.Container({ map: this.map });
    if (Loca.AmbientLight) new Loca.AmbientLight({ intensity: 0.72, color: "#f2f4ef" }, this.loca);
    if (Loca.DirectionalLight) {
      new Loca.DirectionalLight({ intensity: 0.42, color: "#f2f4ef", target: [0, 0, 0], position: [0, -1, 1] }, this.loca);
    }
    this._listen("click", (event) => this._queryFeature(event));
    ["dragstart", "rotatestart", "zoomstart"].forEach((type) => this._listen(type, () => this.cancelCamera()));
    this._syncAnimation();
    return this;
  }

  setEvidence(descriptors = [], layerState = {}, highlightedIds = new Set()) {
    if (!this.loca) return;
    this.layerState = { ...layerState };
    this.highlightedIds = new Set([...highlightedIds].map(String));
    this._clearLayers();

    const siteDescriptor = descriptors.find((item) => item.layer === "parcel" && item.type === "point");
    const sitePoint = pointOf(siteDescriptor);
    const siteFeature = siteDescriptor ? pointFeature(siteDescriptor, { role: "site" }) : null;
    const competitors = descriptors.filter((item) => item.layer === "competitors" && item.type === "point").map((item) => pointFeature(item)).filter(Boolean);
    this.competitorIds = new Set(competitors.map((item) => String(item.properties.id)));
    const resources = descriptors.filter((item) => item.layer === "resources" && item.type === "point").map((item) => pointFeature(item)).filter(Boolean);
    const riskDescriptors = descriptors.filter((item) => item.layer === "risks" && item.type === "point");
    const riskGroups = new Map();
    riskDescriptors.forEach((descriptor) => {
      const point = pointOf(descriptor);
      if (!point) return;
      const key = `${point[0].toFixed(5)},${point[1].toFixed(5)}`;
      const current = riskGroups.get(key) || { descriptor, point, count: 0, labels: [] };
      current.count += 1;
      current.labels.push(descriptor.label || descriptor.id);
      riskGroups.set(key, current);
    });
    const risks = [...riskGroups.values()].map(({ descriptor, count, labels }) => pointFeature(descriptor, { count, labels }));

    if (siteFeature) {
      this._addLayer("site", globalThis.Loca.ScatterLayer, [siteFeature], {
        unit: "meter",
        size: [260, 260],
        borderWidth: 0,
        texture: SITE_TEXTURE,
        duration: 1800,
        animate: !this.reducedMotion && this.quality !== "low",
      }, { zIndex: 130, zooms: [11, 20] });
    }

    if (competitors.length) {
      this._addLayer("heat", globalThis.Loca.HeatMapLayer, competitors, {
        radius: 260,
        unit: "meter",
        height: 180,
        value: (_, feature) => priceOf(feature),
        min: Math.min(...competitors.map(priceOf)),
        max: Math.max(...competitors.map(priceOf)),
        opacity: [0, 0.56],
        gradient: {
          0.2: "rgba(120,172,175,0.08)",
          0.45: "rgba(120,172,175,0.28)",
          0.7: "rgba(200,121,97,0.42)",
          1: "rgba(200,109,96,0.68)",
        },
        difference: false,
      }, { zIndex: 22, zooms: [11, 18], depth: true, opacity: 0.72 });
    }

    if (resources.length) {
      this._addLayer("resources", globalThis.Loca.IconLayer, resources, {
        icon: markerIcon(COLORS.resources, "diamond"),
        iconSize: [16, 16],
        opacity: 0.9,
        unit: "px",
      }, { zIndex: 86, zooms: [13, 20] });
    }

    if (risks.length) {
      this._addLayer("risks", globalThis.Loca.PointLayer, risks, {
        radius: (_, feature) => Math.min(72, 34 + Number(feature.properties.count || 1) * 7),
        blurWidth: (_, feature) => Math.min(58, 24 + Number(feature.properties.count || 1) * 6),
        color: "rgba(200,109,96,0.42)",
        borderWidth: 2,
        borderColor: "rgba(200,109,96,0.84)",
        unit: "px",
      }, { zIndex: 76, zooms: [11, 20], blend: "normal", opacity: 0.9 });
    }

    const selectedCompetitors = competitors.filter((item) => this.highlightedIds.has(String(item.properties.id)));
    if (sitePoint && selectedCompetitors.length) {
      const links = selectedCompetitors
        .slice(0, 1)
        .map((item) => lineFeature(`link-${item.properties.id}`, [sitePoint, item.geometry.coordinates], item.properties));
      this._addLayer("links", globalThis.Loca.LinkLayer, links, {
        lineColors: ["rgba(120,172,175,0.04)", "rgba(120,172,175,0.58)"],
        height: (_, item) => Math.max(120, Math.min(760, Number(item?.distance || 0) * 0.16)),
        smoothSteps: 72,
      }, { zIndex: 58, zooms: [11, 18], opacity: 0.72 });
    }

    const lines = [];
    const pulseLines = [];
    descriptors.forEach((descriptor) => {
      const raw = descriptor.amapCoordinates || descriptor.coordinates || descriptor.data?.coordinates;
      if (!Array.isArray(raw) || raw.length < 2) return;
      const coordinates = descriptor.type === "polygon" && raw.length > 2 ? [...raw, raw[0]] : raw;
      const output = lineFeature(String(descriptor.id), coordinates, { layer: descriptor.layer, altitude: Number(descriptor.data?.altitude || 0) });
      if (descriptor.type === "pulse-line" || descriptor.data?.animated === true) pulseLines.push(output);
      else if (descriptor.type === "line" || descriptor.type === "polygon") lines.push(output);
    });
    if (lines.length) {
      this._addLayer("lines", globalThis.Loca.LineLayer, lines, {
        color: "rgba(200,121,97,0.92)",
        lineWidth: 3,
        borderColor: "rgba(11,15,14,0.72)",
        borderWidth: 1,
        altitude: (_, feature) => Number(feature.properties.altitude || 6),
      }, { zIndex: 118, zooms: [11, 20], opacity: 0.9 });
    }
    if (pulseLines.length) {
      this._addLayer("pulseLines", globalThis.Loca.PulseLineLayer, pulseLines, {
        lineWidth: 3,
        headColor: "rgba(242,244,239,0.92)",
        trailColor: "rgba(120,172,175,0.18)",
        altitude: 16,
        interval: 0.24,
        duration: 2200,
      }, { zIndex: 104, zooms: [11, 20], opacity: 0.84 });
    }
    this._applyVisibility();
    this._syncAnimation();
  }

  setVisibility(layerState = {}, highlightedIds = this.highlightedIds) {
    this.layerState = { ...this.layerState, ...layerState };
    this.highlightedIds = new Set([...highlightedIds].map(String));
    this._applyVisibility();
  }

  setQuality(profile = "balanced") {
    this.quality = profile;
    this._syncAnimation();
  }

  setMode(mode = "3d") {
    this.mode = mode === "2d" ? "2d" : "3d";
    this._syncAnimation();
  }

  animateCamera(center, camera = {}) {
    if (!this.loca?.viewControl || this.reducedMotion || this.quality === "low") {
      this.map.setZoomAndCenter?.(camera.zoom, center);
      this.map.setPitch?.(camera.pitch);
      this.map.setRotation?.(camera.rotation);
      return Promise.resolve({ completed: true, animated: false });
    }
    this.cancelCamera();
    return new Promise((resolve) => {
      this.loca.viewControl.addAnimates([{
        center: { value: center, control: [center, center], timing: [0.42, 0, 0.4, 1], duration: 900 },
        zoom: { value: camera.zoom, control: [[0.25, this.map.getZoom?.() || camera.zoom], [0.7, camera.zoom]], timing: [0.42, 0, 0.4, 1], duration: 900 },
        pitch: { value: camera.pitch, control: [[0.25, this.map.getPitch?.() || 0], [0.7, camera.pitch]], timing: [0.42, 0, 0.4, 1], duration: 900 },
        rotation: { value: camera.rotation, control: [[0.25, this.map.getRotation?.() || 0], [0.7, camera.rotation]], timing: [0.42, 0, 0.4, 1], duration: 900 },
      }], () => resolve({ completed: true, animated: true }));
    });
  }

  cancelCamera() {
    this.loca?.viewControl?.clearAnimates?.();
  }

  destroy() {
    this.cancelCamera();
    this.loca?.animate?.stop?.();
    this.listeners.forEach(([type, listener]) => this.map.off?.(type, listener));
    this.listeners = [];
    this._clearLayers();
    this.loca?.destroy?.();
    this.loca = null;
    this.map = null;
  }

  _addLayer(name, LayerConstructor, features, style, options = {}) {
    if (!LayerConstructor || !features.length) return;
    const source = new globalThis.Loca.GeoJSONSource({ data: featureCollection(features) });
    const layer = new LayerConstructor({ loca: this.loca, visible: true, ...options });
    layer.setSource(source);
    layer.setStyle(style);
    this.sources.add(source);
    this.layers.set(name, layer);
  }

  _applyVisibility() {
    const competitorFocus = [...this.highlightedIds].some((id) => this.competitorIds.has(String(id)));
    const visibility = {
      site: this.layerState.parcel !== false,
      resources: this.layerState.resources !== false,
      risks: this.layerState.risks !== false,
      links: this.layerState.links === true && competitorFocus,
      heat: this.layerState.heat === true,
      lines: this.layerState.lines !== false && this.layerState.parcel !== false,
      pulseLines: this.layerState.pulseLines === true && this.layerState.resources !== false,
    };
    const duration = this.reducedMotion ? 0 : 260;
    this.layers.forEach((layer, name) => visibility[name] === false ? layer.hide?.(duration) : layer.show?.(duration));
  }

  _queryFeature(event) {
    const pixel = event?.pixel?.toArray?.();
    if (!pixel) return;
    for (const [name, layer] of [...this.layers.entries()].reverse()) {
      if (["heat", "links", "site"].includes(name)) continue;
      const result = layer.queryFeature?.(pixel);
      if (result) {
        this.onFeature?.(result, event);
        break;
      }
    }
  }

  _listen(type, listener) {
    this.map.on?.(type, listener);
    this.listeners.push([type, listener]);
  }

  _syncAnimation() {
    if (!this.loca?.animate) return;
    if (this.mode === "2d" || this.reducedMotion || this.quality === "low" || document.hidden) this.loca.animate.pause?.();
    else this.loca.animate.start?.();
  }

  _clearLayers() {
    this.layers.forEach((layer) => layer.destroy?.());
    this.layers.clear();
    this.sources.forEach((source) => source.destroy?.());
    this.sources.clear();
  }
}
