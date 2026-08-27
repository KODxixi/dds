export const DEFAULT_PATHS = Object.freeze({
  apiBase: "/api/v2",
  assetBase: "/dds/assets/",
  cesiumBase: "/dds/vendor/cesium/",
});

export const QUALITY_PROFILES = Object.freeze({
  high: Object.freeze({
    resolutionScale: 1,
    maximumScreenSpaceError: 8,
    shadows: true,
    terrain: true,
    labelDensity: "full",
    cameraDuration: 2.4,
  }),
  balanced: Object.freeze({
    resolutionScale: 0.9,
    maximumScreenSpaceError: 16,
    shadows: false,
    terrain: true,
    labelDensity: "limited",
    cameraDuration: 1.8,
  }),
  low: Object.freeze({
    resolutionScale: 0.72,
    maximumScreenSpaceError: 32,
    shadows: false,
    terrain: false,
    labelDensity: "minimal",
    cameraDuration: 0.8,
  }),
});

export const CAMERA_PRESETS = Object.freeze({
  globe: Object.freeze({ height: 18_500_000, pitch: -90, heading: 0 }),
  city: Object.freeze({ height: 42_000, pitch: -58, heading: 0 }),
  site: Object.freeze({ height: 2_400, pitch: -48, heading: 8 }),
  context: Object.freeze({ height: 8_500, pitch: -55, heading: 18 }),
  evidence: Object.freeze({ height: 12_500, pitch: -62, heading: 0 }),
  presentation: Object.freeze({ height: 4_800, pitch: -42, heading: 25 }),
});

export const SCENE_DIRECTIVE_TYPES = Object.freeze([
  "fly_to",
  "focus_bounds",
  "set_layers",
  "highlight",
  "restore_bookmark",
  "play_tour",
  "set_quality",
]);

const ensureLeadingSlash = (value) => {
  const text = String(value || "").trim();
  if (!text) return "/";
  if (/^[a-z][a-z\d+.-]*:/i.test(text)) return text;
  return text.startsWith("/") ? text : `/${text}`;
};

const ensureTrailingSlash = (value) => {
  const text = ensureLeadingSlash(value);
  return text.endsWith("/") ? text : `${text}/`;
};

export function joinBase(base, path = "") {
  const cleanPath = String(path || "").replace(/^\/+/, "");
  return `${ensureTrailingSlash(base)}${cleanPath}`;
}

export function readProjectId(pathname = globalThis.location?.pathname || "") {
  const match = String(pathname).match(/\/projects\/([^/]+)\/(?:report|map)\/?$/i);
  return match ? decodeURIComponent(match[1]) : null;
}

export function chooseQualityProfile({ width, deviceMemory, reducedMotion } = {}) {
  const viewportWidth = Number(width || globalThis.innerWidth || 1280);
  const memory = Number(deviceMemory || globalThis.navigator?.deviceMemory || 4);
  const reduce = reducedMotion ?? globalThis.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
  if (viewportWidth < 768 || memory <= 2 || reduce) return "low";
  if (viewportWidth >= 1440 && memory >= 8) return "high";
  return "balanced";
}

function normalizePublicScene(scene = {}) {
  const quality = QUALITY_PROFILES[scene.quality] ? scene.quality : undefined;
  return {
    ...scene,
    imageryUrl: scene.imageryUrl || scene.imagery_url || "",
    terrainUrl: scene.terrainUrl || scene.terrain_url || "",
    buildingsUrl: scene.buildingsUrl || scene.buildings_url || "",
    quality,
    ionToken: scene.ionToken || scene.ion_token || scene.cesium_ion_token || '',
  };
}

export function createConfig(options = {}) {
  const injected = globalThis.DDS_DECISION_FIELD_CONFIG || {};
  const root = options.root || null;
  const dataset = root?.dataset || {};
  const apiBase = ensureLeadingSlash(
    options.apiBase || dataset.apiBase || injected.apiBase || DEFAULT_PATHS.apiBase,
  ).replace(/\/$/, "");
  const assetBase = ensureTrailingSlash(
    options.assetBase || dataset.assetBase || injected.assetBase || DEFAULT_PATHS.assetBase,
  );
  const cesiumBase = ensureTrailingSlash(
    options.cesiumBase || dataset.cesiumBase || injected.cesiumBase || DEFAULT_PATHS.cesiumBase,
  );
  const quality = options.quality
    || dataset.quality
    || injected.quality
    || chooseQualityProfile();
  const projectId = options.projectId
    || dataset.projectId
    || injected.projectId
    || readProjectId();

  return Object.freeze({
    apiBase,
    assetBase,
    cesiumBase,
    projectId,
    hostOrigin: options.hostOrigin || dataset.hostOrigin || injected.hostOrigin || globalThis.location?.origin || "*",
    quality: QUALITY_PROFILES[quality] ? quality : "balanced",
    reducedMotion: globalThis.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches || false,
    pollInterval: Number(options.pollInterval || injected.pollInterval || 1500),
    scene: normalizePublicScene({ ...(injected.scene || {}), ...(options.scene || {}) }),
    amap: options.amap || injected.amap || null,
    asset(path) {
      return joinBase(assetBase, path);
    },
    reportUrl(id) {
      return `/projects/${encodeURIComponent(id)}/report`;
    },
    mapUrl(value) {
      if (typeof value === "string") return `/projects/${encodeURIComponent(value)}/map`;
      const location = value || {};
      const point = location.wgs84 || location.gcj02 || location;
      const lng = Number(point?.lng);
      const lat = Number(point?.lat);
      const params = new URLSearchParams();
      if (Number.isFinite(lng) && Number.isFinite(lat)) {
        params.set("lng", lng.toFixed(7));
        params.set("lat", lat.toFixed(7));
        params.set("crs", location.wgs84 ? "wgs84" : "gcj02");
      }
      const label = location.display_name || location.address;
      if (label) params.set("label", label);
      if (location.city) params.set("city", location.city);
      return `/map${params.size ? `?${params}` : ""}`;
    },
  });
}
