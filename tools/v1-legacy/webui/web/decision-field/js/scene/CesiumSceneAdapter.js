import { CAMERA_PRESETS, QUALITY_PROFILES } from "../config.js";
import { AmapDecisionLayers } from "./AmapDecisionLayers.js";
import { LocaDataScene } from "./LocaDataScene.js";

const FALLBACK_LANDMASSES = [
  [[-168, 70], [-138, 58], [-125, 50], [-116, 32], [-97, 18], [-82, 25], [-64, 46], [-61, 59], [-95, 72], [-130, 72]],
  [[-81, 12], [-71, 6], [-61, -8], [-53, -28], [-66, -55], [-75, -40], [-80, -12]],
  [[-10, 36], [4, 45], [24, 58], [55, 61], [91, 74], [139, 55], [161, 56], [147, 38], [119, 20], [103, 2], [80, 8], [61, 27], [42, 30], [35, 44], [20, 39]],
  [[-17, 34], [8, 36], [34, 31], [51, 12], [42, -12], [31, -34], [17, -35], [4, -17], [-8, 5]],
  [[112, -11], [131, -12], [153, -28], [146, -43], [119, -35]],
  [[-52, 60], [-42, 82], [-20, 75], [-30, 60]],
];

const COLOR_BY_LAYER = Object.freeze({
  parcel: "#c87961",
  searchArea: "#c87961",
  competitors: "#78acaf",
  resources: "#91aa96",
  risks: "#c86d60",
  evidence: "#dce1da",
});

const AMAP_CAMERA = Object.freeze({
  zoom: 16.8,
  pitch: 58,
  rotation: -14,
});

const AMAP_PRESETS = Object.freeze({
  globe: Object.freeze({ zoom: 5, pitch: 0, rotation: 0 }),
  city: Object.freeze({ zoom: 16.8, pitch: 58, rotation: -14 }),
  context: Object.freeze({ zoom: 13.4, pitch: 38, rotation: -8 }),
  evidence: Object.freeze({ zoom: 13.2, pitch: 36, rotation: -8 }),
  presentation: Object.freeze({ zoom: 14.3, pitch: 42, rotation: -10 }),
});

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function normalizeTarget(target) {
  const point = target?.wgs84 || target?.position || target || {};
  const lng = Number(point.lng ?? point.lon ?? point.longitude);
  const lat = Number(point.lat ?? point.latitude);
  if (!Number.isFinite(lng) || !Number.isFinite(lat)) throw new TypeError("target requires WGS84 lng/lat");
  return {
    lng: clamp(lng, -180, 180),
    lat: clamp(lat, -90, 90),
    height: Number(point.height ?? target?.height) || undefined,
  };
}

function screenPoint(position, element) {
  if (!position || !element) return null;
  const rect = element.getBoundingClientRect();
  if (Number.isFinite(position.clientX) && Number.isFinite(position.clientY)) {
    return { x: position.clientX - rect.left, y: position.clientY - rect.top };
  }
  if (Number.isFinite(position.x) && Number.isFinite(position.y)) return { x: position.x, y: position.y };
  return null;
}

function publicLayerState(state) {
  const result = {};
  Object.entries(state || {}).forEach(([key, value]) => {
    if (key !== "evidence" && typeof value === "boolean") result[key] = value;
  });
  return result;
}

export class CesiumSceneAdapter {
  constructor({ container = null, fallbackCanvas = null, onStatus = null, onUserInteraction = null } = {}) {
    this.container = container;
    this.fallbackCanvas = fallbackCanvas;
    this.onStatus = onStatus;
    this.onUserInteraction = onUserInteraction;
    this.viewer = null;
    this.mode = "uninitialized";
    this.config = {};
    this.quality = "balanced";
    this.layerState = { parcel: true, competitors: true, resources: true, risks: true };
    this.evidence = [];
    this.evidenceIds = new Set();
    this.highlightedIds = new Set();
    this.tilesets = [];
    this.flightGeneration = 0;
    this.destroyed = false;
    this._resizeObserver = null;
    this._fallbackListeners = [];
    this._contextListeners = [];
    this._fallbackView = { lng: 105, lat: 35, spanLng: 360, spanLat: 170 };
    this._fallbackDrag = null;
    this.activeEngine = "cesium";
    this.amapOverlays = new Map();
    this.amapInfoWindow = null;
    this.amapFocus = null;
    this.amapCameraPrimed = false;
    this.amapMode = "2d";
    this.amapViewMode = null;
    this.amapRebuild = null;
    this.amapContainer = null;
    this.amapDecisionLayers = null;
    this.locaReady = null;
    this.locaScene = null;
  }

  async initialize(config = {}) {
    this.config = { ...config };
    this.container = config.container || this.container;
    this.fallbackCanvas = config.fallbackCanvas || this.fallbackCanvas;
    this.onStatus = config.onStatus || this.onStatus;
    this.onUserInteraction = config.onUserInteraction || this.onUserInteraction;
    this.onFeatureSelected = config.onFeatureSelected || this.onFeatureSelected;
    this.onPoiUpdate = config.onPoiUpdate || this.onPoiUpdate;
    this.amapConfig = config.amap || null;
    this.destroyed = false;
    if (!this.container) throw new Error("Scene container is required");

    this._observeResize();
    if (config.force2D || !globalThis.Cesium || !this._supportsWebGL()) {
      const reason = config.force2D ? "已切换二维概览" : "WebGL 不可用，使用二维概览";
      this._activateFallback(reason);
      return { mode: this.mode, reason };
    }

    try {
      await this._initializeCesium(config);
      this.mode = "3d";
      this._emitStatus({ mode: "3d", label: this._isBaseScene() ? "基础场景" : "三维场景", tone: "ok" });
      this.setQuality(config.quality || this.quality);
      return { mode: this.mode, baseScene: this._isBaseScene() };
    } catch (error) {
      this._activateFallback(`三维初始化失败：${error.message || error}`);
      return { mode: this.mode, reason: String(error.message || error), error };
    }
  }

  async _initializeCesium(config) {
    const Cesium = globalThis.Cesium;
    globalThis.CESIUM_BASE_URL = config.cesiumBase || globalThis.CESIUM_BASE_URL || "/dds/vendor/cesium/";
    if (config.ionToken && Cesium.Ion) Cesium.Ion.defaultAccessToken = config.ionToken;

    this.container.hidden = false;
    if (this.fallbackCanvas) this.fallbackCanvas.hidden = true;
    const ellipsoid = new Cesium.EllipsoidTerrainProvider();
    this.viewer = new Cesium.Viewer(this.container, {
      animation: false,
      baseLayer: false,
      baseLayerPicker: false,
      fullscreenButton: false,
      geocoder: false,
      homeButton: false,
      imageryProvider: false,
      infoBox: false,
      navigationHelpButton: false,
      sceneModePicker: false,
      selectionIndicator: false,
      shouldAnimate: false,
      timeline: false,
      terrainProvider: ellipsoid,
      requestRenderMode: !config.hero,
      maximumRenderTimeChange: Number.POSITIVE_INFINITY,
      contextOptions: {
        webgl: {
          alpha: false,
          antialias: true,
          preserveDrawingBuffer: false,
          powerPreference: "high-performance",
        },
      },
    });

    const { scene } = this.viewer;
    scene.backgroundColor = Cesium.Color.fromCssColorString("#0b0f0e");
    scene.globe.baseColor = Cesium.Color.fromCssColorString("#1a211e");
    scene.globe.showGroundAtmosphere = true;
    scene.highDynamicRange = true;
    scene.fog.enabled = true;
    scene.screenSpaceCameraController.enableCollisionDetection = true;
    this.viewer.camera.setView({
      destination: Cesium.Cartesian3.fromDegrees(105, 35, CAMERA_PRESETS.globe.height),
      orientation: { heading: 0, pitch: Cesium.Math.toRadians(-90), roll: 0 },
    });

    await this._configureImagery(config);
    if (config.hero) this._styleHeroGlobe(scene);
    await this._configureTerrain(config);
    await this._configureBuildings(config);
    if (config.hero) await this._primeCesiumFrame(scene);
    this._bindContextEvents();
    scene.requestRender();
  }

  _styleHeroGlobe(scene) {
    const globe = scene.globe;
    globe.showGroundAtmosphere = true;
    globe.atmosphereSaturationShift = -1;
    globe.atmosphereBrightnessShift = -0.4;
    globe.atmosphereLightIntensity = 6;
    if (scene.skyAtmosphere) {
      scene.skyAtmosphere.saturationShift = -1;
      scene.skyAtmosphere.brightnessShift = -0.35;
      scene.skyAtmosphere.atmosphereLightIntensity = 18;
    }
  }

  async _primeCesiumFrame(scene) {
    await new Promise((resolve) => {
      let frameCount = 0;
      let settled = false;
      const timeout = globalThis.setTimeout(finish, 900);
      function finish() {
        if (settled) return;
        settled = true;
        globalThis.clearTimeout(timeout);
        resolve();
      }
      function nextFrame() {
        frameCount += 1;
        if (frameCount >= 8 && scene.globe.tilesLoaded) finish();
        else globalThis.requestAnimationFrame(nextFrame);
      }
      globalThis.requestAnimationFrame(nextFrame);
    });
    if (this.destroyed) return;
    scene.requestRenderMode = true;
    scene.requestRender();
  }

  async enableAmap3D(location = {}) {
    const result = await this.enableAmap(location, { initialMode: "2d" });
    this.setAmapMode("3d", { animate: !this.config.reducedMotion });
    return { ...result, progressive: true };
  }

  async enableAmap(location = {}, { initialMode = "2d" } = {}) {
    const point = location.gcj02 || location.wgs84 || location;
    const lng = Number(point.lng); const lat = Number(point.lat);
    if (!Number.isFinite(lng) || !Number.isFinite(lat)) throw new TypeError("高德地图定位需要有效坐标");
    if (!this.amapConfig?.key) throw new Error("高德 JS Key 未配置");
    this.setFallbackLocation({ lng, lat });
    this._emitStatus({ mode: "amap-loading", label: "正在异步加载高德地图", tone: "loading" });
    const AMap = await this._loadAmap();
    const host = this.container?.parentElement || this.container;
    this.container.hidden = true;
    if (this.fallbackCanvas) this.fallbackCanvas.hidden = true;
    let amapContainer = host.querySelector(".amap-scene-canvas");
    if (!amapContainer) {
      amapContainer = document.createElement("div");
      amapContainer.className = "amap-scene-canvas";
      amapContainer.dataset.state = "loading";
      amapContainer.setAttribute("aria-label", "高德项目地图");
      host.insertBefore(amapContainer, this.container);
    }
    this.amapContainer = amapContainer;
    amapContainer.hidden = false;
    const focusKey = `${lng.toFixed(6)},${lat.toFixed(6)}`;
    const locationChanged = focusKey !== this.amapFocus?.key;
    this.amapFocus = { key: focusKey, lng, lat };
    if (locationChanged) this.amapCameraPrimed = false;
    if (!this.amapMap) {
      this.amapMode = initialMode === "3d" ? "3d" : "2d";
      this.amapMap = this._createAmapMap(AMap, amapContainer, this.amapMode, [lng, lat]);
    } else {
      this.amapMap.setZoomAndCenter(initialMode === "3d" ? AMAP_CAMERA.zoom : 14, [lng, lat]);
    }
    if (!this.amapDecisionLayers) {
      this.amapDecisionLayers = this._createAmapDecisionLayers();
    }
    this.activeEngine = "amap";
    await this.setAmapMode(initialMode, { animate: false });
    this._reconcileAmapEvidence();
    this._ensureLocaScene().catch((error) => {
      this._emitStatus({ mode: `amap-${this.amapMode}`, label: "高德基础地图已就绪", tone: "warning", reason: `Loca 演绎层已降级：${error.message}` });
    });
    globalThis.setTimeout(() => {
      if (this.amapContainer?.dataset.state === "loading") this.amapContainer.dataset.state = "ready";
    }, 8000);
    return { completed: true, engine: "amap", mode: this.amapMode };
  }

  _createAmapMap(AMap, container, mode, center) {
    const is3d = mode === "3d";
    this.amapViewMode = mode;
    container.dataset.state = "loading";
    const map = new AMap.Map(container, {
      viewMode: is3d ? "3D" : "2D",
      pitch: is3d ? AMAP_CAMERA.pitch : 0,
      rotation: is3d ? AMAP_CAMERA.rotation : 0,
      zoom: is3d ? AMAP_CAMERA.zoom : 14,
      zooms: [3, 20],
      center,
      rotateEnable: is3d,
      pitchEnable: is3d,
      animateEnable: !this.config.reducedMotion,
      showBuildingBlock: false,
      showIndoorMap: false,
      showLabel: true,
      features: is3d ? ["bg", "road", "point", "building"] : ["bg", "road", "point"],
      mapStyle: is3d ? "amap://styles/dark" : "amap://styles/normal",
      skyColor: "#080b0a",
      wallColor: "#303934",
      roofColor: "#4a554f",
    });
    map.on("complete", () => {
      container.dataset.state = "ready";
      this._emitStatus({ mode: `amap-${this.amapMode}`, label: is3d ? "高德三维决策场" : "高德二维分析场", tone: "ok" });
    });
    map.on("mousedown", () => {
      this.locaScene?.cancelCamera();
      this.onUserInteraction?.("amap_pointer");
    });
    return map;
  }

  _createAmapDecisionLayers() {
    return new AmapDecisionLayers({
      map: this.amapMap,
      mapStyle: this.amapConfig?.mapStyle || "",
      onSelect: ({ descriptor, position }) => {
        if (descriptor && position) this._openAmapDescriptor(descriptor, position);
        if (descriptor) this.onFeatureSelected?.(String(descriptor.id));
      },
      onPoiUpdate: (payload) => this.onPoiUpdate?.(payload),
      onStatus: (status) => this._emitStatus({ mode: `amap-${this.amapMode}`, ...status }),
    }).initialize();
  }

  async _rebuildAmapRuntime(mode) {
    if (this.amapRebuild) return this.amapRebuild;
    this.amapRebuild = (async () => {
      const AMap = await this._loadAmap();
      const center = this.amapMap?.getCenter?.()?.toArray?.() || [this.amapFocus?.lng, this.amapFocus?.lat];
      const basemap = this.amapDecisionLayers?.basemap || "color";
      this.amapOverlays.forEach((overlay) => this.amapMap?.remove?.(overlay));
      this.amapOverlays.clear();
      this.amapDecisionLayers?.destroy();
      this.amapDecisionLayers = null;
      this.locaScene?.destroy();
      this.locaScene = null;
      this.amapMap?.destroy?.();
      this.amapContainer.replaceChildren();
      this.amapCameraPrimed = false;
      this.amapMode = mode;
      this.amapMap = this._createAmapMap(AMap, this.amapContainer, mode, center);
      this.amapDecisionLayers = this._createAmapDecisionLayers();
      this.amapDecisionLayers.setBasemap(mode === "3d" ? "color" : basemap);
      this._reconcileAmapEvidence();
      this._ensureLocaScene().catch(() => null);
      return mode;
    })().finally(() => { this.amapRebuild = null; });
    return this.amapRebuild;
  }

  async setAmapMode(mode = "2d", { animate = true } = {}) {
    if (!this.amapMap) return this.amapMode;
    const nextMode = mode === "3d" ? "3d" : "2d";
    if (this.amapViewMode !== nextMode) return this._rebuildAmapRuntime(nextMode);
    this.amapMode = nextMode;
    const is3d = this.amapMode === "3d";
    this.amapMap.setPitch?.(is3d ? AMAP_CAMERA.pitch : 0);
    this.amapMap.setRotation?.(is3d ? AMAP_CAMERA.rotation : 0);
    if (is3d && Number(this.amapMap.getZoom?.() || 0) < 16.4 && this.amapFocus) {
      this.amapMap.setZoomAndCenter?.(AMAP_CAMERA.zoom, [this.amapFocus.lng, this.amapFocus.lat]);
    }
    this.amapMap.setStatus?.({ rotateEnable: is3d, pitchEnable: is3d, animateEnable: Boolean(animate) });
    this.amapMap.setFeatures?.(is3d ? ["bg", "road", "point", "building"] : ["bg", "road", "point"]);
    this.amapDecisionLayers?.setMode(this.amapMode);
    this.locaScene?.setMode?.(this.amapMode);
    this.mode = `amap-${this.amapMode}`;
    return this.amapMode;
  }

  async _ensureLocaScene() {
    if (this.locaScene) return this.locaScene;
    await this._loadLoca();
    if (this.destroyed || !this.amapMap || !globalThis.Loca?.Container) return null;
    this.locaScene = new LocaDataScene({
      map: this.amapMap,
      reducedMotion: this.config.reducedMotion,
      onFeature: (feature, event) => {
        const id = String(feature?.properties?.id || "");
        const descriptor = this.evidence.find((item) => String(item.id) === id);
        const position = event?.lnglat?.toArray?.() || feature?.geometry?.coordinates;
        if (descriptor && position) this._openAmapDescriptor(descriptor, position);
        if (descriptor) this.onFeatureSelected?.(String(descriptor.id));
      },
    }).initialize();
    this.locaScene.setQuality(this.quality);
    this.locaScene.setMode(this.amapMode);
    this.locaScene.setEvidence(this.evidence, this.layerState, this.highlightedIds);
    return this.locaScene;
  }

  _loadAmap() {
    if (globalThis.AMap) return Promise.resolve(globalThis.AMap);
    if (globalThis.__DDS_AMAP_READY__) return globalThis.__DDS_AMAP_READY__;
    if (this.amapReady) return this.amapReady;
    globalThis._AMapSecurityConfig = { securityJsCode: this.amapConfig.securityCode || "" };
    const callbackName = `__ddsAmapLoaded_${Date.now()}_${Math.random().toString(36).slice(2)}`;
    this.amapReady = new Promise((resolve, reject) => {
      let settled = false;
      let script = null;
      const finish = (error = null) => {
        if (settled) return;
        settled = true;
        globalThis.clearTimeout(timer);
        delete globalThis[callbackName];
        script?.remove?.();
        if (error) reject(error);
        else if (globalThis.AMap) resolve(globalThis.AMap);
        else reject(new Error("高德 JS API 未返回 AMap"));
      };
      const timer = globalThis.setTimeout(() => finish(new Error("高德 JS API 异步加载超时")), 10000);
      globalThis[callbackName] = () => finish();
      script = document.createElement("script");
      script.id = "dds-amap-jsapi";
      script.dataset.ddsAmapLoader = "true";
      script.charset = "utf-8";
      script.async = true;
      script.src = `https://webapi.amap.com/maps?v=2.0&key=${encodeURIComponent(this.amapConfig.key)}&callback=${callbackName}`;
      script.onload = () => { if (globalThis.AMap) finish(); };
      script.onerror = () => finish(new Error("高德地图脚本加载失败"));
      document.head.appendChild(script);
    });
    this.amapReady = this.amapReady.catch((error) => {
      globalThis.__DDS_AMAP_READY__ = null;
      this.amapReady = null;
      throw error;
    });
    globalThis.__DDS_AMAP_READY__ = this.amapReady;
    return this.amapReady;
  }

  setFallbackLocation(location = {}) {
    const point = normalizeTarget(location?.gcj02 || location?.wgs84 || location);
    this._fallbackView = {
      lng: point.lng,
      lat: point.lat,
      spanLng: 0.12,
      spanLat: 0.075,
    };
    this._resizeFallback();
    this._drawFallback();
  }

  _loadLoca() {
    if (globalThis.Loca?.Container) return Promise.resolve(globalThis.Loca);
    if (globalThis.__DDS_LOCA_READY__) return globalThis.__DDS_LOCA_READY__;
    if (this.locaReady) return this.locaReady;
    this.locaReady = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "https://webapi.amap.com/loca?v=2.0.0&key=" + encodeURIComponent(this.amapConfig.key);
      script.async = true;
      const timer = globalThis.setTimeout(() => reject(new Error("Loca 2.0 加载超时")), 12000);
      script.onload = () => {
        globalThis.clearTimeout(timer);
        if (globalThis.Loca?.Container) resolve(globalThis.Loca);
        else reject(new Error("Loca 2.0 运行时不完整"));
      };
      script.onerror = () => {
        globalThis.clearTimeout(timer);
        reject(new Error("Loca 2.0 加载失败"));
      };
      document.head.appendChild(script);
    });
    this.locaReady = this.locaReady.catch((error) => {
      globalThis.__DDS_LOCA_READY__ = null;
      this.locaReady = null;
      throw error;
    });
    globalThis.__DDS_LOCA_READY__ = this.locaReady;
    return this.locaReady;
  }

  async _configureImagery(config) {
    const Cesium = globalThis.Cesium;
    try {
      let provider;
      const worldTextureProvider = async () => {
        try {
          return typeof Cesium.SingleTileImageryProvider.fromUrl === "function"
            ? await Cesium.SingleTileImageryProvider.fromUrl(config.worldTextureUrl)
            : new Cesium.SingleTileImageryProvider({ url: config.worldTextureUrl });
        } catch {
          const naturalEarthUrl = Cesium.buildModuleUrl("Assets/Textures/NaturalEarthII");
          return typeof Cesium.TileMapServiceImageryProvider.fromUrl === "function"
            ? await Cesium.TileMapServiceImageryProvider.fromUrl(naturalEarthUrl)
            : new Cesium.TileMapServiceImageryProvider({ url: naturalEarthUrl });
        }
      };
      if (config.preferWorldTexture && config.worldTextureUrl) {
        provider = await worldTextureProvider();
      } else if (config.imageryProvider) {
        provider = config.imageryProvider;
      } else if (config.imageryUrl) {
        provider = new Cesium.UrlTemplateImageryProvider({
          url: config.imageryUrl,
          maximumLevel: Number(config.imageryMaximumLevel || 18),
          credit: config.imageryCredit || undefined,
        });
      } else if (config.worldTextureUrl) {
        provider = await worldTextureProvider();
      } else {
        const naturalEarthUrl = Cesium.buildModuleUrl("Assets/Textures/NaturalEarthII");
        provider = typeof Cesium.TileMapServiceImageryProvider.fromUrl === "function"
          ? await Cesium.TileMapServiceImageryProvider.fromUrl(naturalEarthUrl)
          : new Cesium.TileMapServiceImageryProvider({ url: naturalEarthUrl });
      }
      if (provider) {
        const layer = this.viewer.imageryLayers.addImageryProvider(provider);
        if (config.hero) {
          layer.saturation = 0.05;
          layer.brightness = 0.55;
          layer.contrast = 1.2;
          layer.gamma = 0.9;
        }
      }
    } catch (error) {
      this._emitStatus({ mode: "3d", label: "基础球体 / 影像待补", tone: "warning", reason: error.message });
    }
  }

  async _configureTerrain(config) {
    const Cesium = globalThis.Cesium;
    if (!config.terrainUrl && !config.ionToken) return;
    try {
      if (config.terrainProvider) {
        this.viewer.terrainProvider = config.terrainProvider;
      } else if (config.terrainUrl && Cesium.CesiumTerrainProvider?.fromUrl) {
        this.viewer.terrainProvider = await Cesium.CesiumTerrainProvider.fromUrl(config.terrainUrl);
      } else if (config.terrainUrl) {
        this.viewer.terrainProvider = new Cesium.CesiumTerrainProvider({ url: config.terrainUrl });
      } else if (typeof Cesium.createWorldTerrainAsync === "function") {
        this.viewer.terrainProvider = await Cesium.createWorldTerrainAsync();
      } else if (typeof Cesium.createWorldTerrain === "function") {
        this.viewer.terrainProvider = Cesium.createWorldTerrain();
      }
    } catch (error) {
      this._emitStatus({ mode: "3d", label: "椭球地形", tone: "warning", reason: error.message });
    }
  }

  async _configureBuildings(config) {
    const Cesium = globalThis.Cesium;
    try {
      let tileset = null;
      if (config.buildingsUrl && Cesium.Cesium3DTileset?.fromUrl) {
        tileset = await Cesium.Cesium3DTileset.fromUrl(config.buildingsUrl);
      } else if (config.buildingsUrl) {
        tileset = new Cesium.Cesium3DTileset({ url: config.buildingsUrl });
      } else if (config.ionToken && typeof Cesium.createOsmBuildingsAsync === "function") {
        tileset = await Cesium.createOsmBuildingsAsync();
      } else if (config.ionToken && typeof Cesium.createOsmBuildings === "function") {
        tileset = Cesium.createOsmBuildings();
      }
      if (tileset) {
        this.viewer.scene.primitives.add(tileset);
        this.tilesets.push(tileset);
      }
    } catch (error) {
      this._emitStatus({ mode: "3d", label: "建筑图层待补", tone: "warning", reason: error.message });
    }
  }

  _isBaseScene() {
    return !this.config.ionToken && !this.config.terrainUrl && !this.config.buildingsUrl;
  }

  _supportsWebGL() {
    try {
      const canvas = document.createElement("canvas");
      return Boolean(canvas.getContext("webgl2") || canvas.getContext("webgl") || canvas.getContext("experimental-webgl"));
    } catch {
      return false;
    }
  }

  _activateFallback(reason) {
    this.mode = "2d";
    this.quality = QUALITY_PROFILES[this.config.quality] ? this.config.quality : this.quality;
    this.viewer?.destroy?.();
    this.amapInfoWindow?.close?.();
    this.amapOverlays.forEach((overlay) => this.amapMap?.remove?.(overlay));
    this.amapOverlays.clear();
    this.amapDecisionLayers?.destroy();
    this.amapDecisionLayers = null;
    this.locaScene?.destroy();
    this.locaScene = null;
    this.amapMap?.destroy?.();
    this.amapMap = null;
    this.viewer = null;
    this.container.hidden = true;
    if (!this.fallbackCanvas) {
      this.fallbackCanvas = document.createElement("canvas");
      this.fallbackCanvas.className = "fallback-map";
      this.fallbackCanvas.setAttribute("aria-label", "二维地图概览");
      this.container.insertAdjacentElement("afterend", this.fallbackCanvas);
    }
    this.fallbackCanvas.hidden = false;
    this._bindFallbackEvents();
    this._resizeFallback();
    this._drawFallback();
    this._emitStatus({ mode: "2d", label: "二维概览", tone: "warning", reason });
  }

  _observeResize() {
    this._resizeObserver?.disconnect();
    if (typeof globalThis.ResizeObserver !== "function") return;
    const target = this.container?.parentElement || this.container;
    this._resizeObserver = new ResizeObserver(() => {
      this.viewer?.resize?.();
      if (this.mode === "2d") {
        this._resizeFallback();
        this._drawFallback();
      }
    });
    this._resizeObserver.observe(target);
  }

  _bindContextEvents() {
    const canvas = this.viewer?.scene?.canvas;
    if (!canvas) return;
    const lost = (event) => {
      event.preventDefault();
      this.cancelFlight();
      this._emitStatus({ mode: "paused", label: "WebGL 上下文中断", tone: "warning", reason: "context_lost" });
    };
    const restored = () => {
      this.setQuality(this.quality);
      this.viewer?.scene?.requestRender?.();
      this._emitStatus({ mode: "3d", label: "三维场景已恢复", tone: "ok", reason: "context_restored" });
    };
    canvas.addEventListener("webglcontextlost", lost, false);
    canvas.addEventListener("webglcontextrestored", restored, false);
    this._contextListeners.push([canvas, "webglcontextlost", lost], [canvas, "webglcontextrestored", restored]);
  }

  _bindFallbackEvents() {
    this._unbindFallbackEvents();
    const canvas = this.fallbackCanvas;
    if (!canvas) return;
    const down = (event) => {
      canvas.setPointerCapture?.(event.pointerId);
      this._fallbackDrag = {
        x: event.clientX,
        y: event.clientY,
        lng: this._fallbackView.lng,
        lat: this._fallbackView.lat,
        moved: false,
      };
      this.onUserInteraction?.("pointer");
    };
    const move = (event) => {
      if (!this._fallbackDrag) return;
      const rect = canvas.getBoundingClientRect();
      const dx = event.clientX - this._fallbackDrag.x;
      const dy = event.clientY - this._fallbackDrag.y;
      if (Math.hypot(dx, dy) > 3) this._fallbackDrag.moved = true;
      this._fallbackView.lng = clamp(this._fallbackDrag.lng - (dx / rect.width) * this._fallbackView.spanLng, -180, 180);
      this._fallbackView.lat = clamp(this._fallbackDrag.lat + (dy / rect.height) * this._fallbackView.spanLat, -85, 85);
      this._drawFallback();
    };
    const up = (event) => {
      canvas.releasePointerCapture?.(event.pointerId);
      this._fallbackDrag = null;
    };
    const wheel = (event) => {
      event.preventDefault();
      const factor = event.deltaY > 0 ? 1.2 : 0.82;
      this._fallbackView.spanLng = clamp(this._fallbackView.spanLng * factor, 0.02, 360);
      this._fallbackView.spanLat = clamp(this._fallbackView.spanLat * factor, 0.01, 170);
      this._drawFallback();
      this.onUserInteraction?.("wheel");
    };
    [["pointerdown", down], ["pointermove", move], ["pointerup", up], ["pointercancel", up], ["wheel", wheel]].forEach(([type, listener]) => {
      canvas.addEventListener(type, listener, type === "wheel" ? { passive: false } : undefined);
      this._fallbackListeners.push([canvas, type, listener]);
    });
  }

  _unbindFallbackEvents() {
    this._fallbackListeners.forEach(([target, type, listener]) => target.removeEventListener(type, listener));
    this._fallbackListeners = [];
  }

  _resizeFallback() {
    const canvas = this.fallbackCanvas;
    if (!canvas || canvas.hidden) return;
    const rect = canvas.getBoundingClientRect();
    const ratio = Math.min(2, globalThis.devicePixelRatio || 1);
    const width = Math.max(1, Math.round(rect.width * ratio));
    const height = Math.max(1, Math.round(rect.height * ratio));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
  }

  _fallbackProject(lng, lat, width, height) {
    const { spanLng, spanLat } = this._fallbackView;
    return {
      x: ((lng - (this._fallbackView.lng - spanLng / 2)) / spanLng) * width,
      y: (((this._fallbackView.lat + spanLat / 2) - lat) / spanLat) * height,
    };
  }

  _drawFallback() {
    const canvas = this.fallbackCanvas;
    const context = canvas?.getContext("2d");
    if (!context) return;
    const { width, height } = canvas;
    context.clearRect(0, 0, width, height);
    context.fillStyle = "#111916";
    context.fillRect(0, 0, width, height);

    context.strokeStyle = "rgba(220,225,218,0.11)";
    context.lineWidth = 1;
    const gridStep = this._fallbackView.spanLng > 100 ? 30 : this._fallbackView.spanLng > 10 ? 5 : this._fallbackView.spanLng > 1 ? 0.5 : 0.05;
    const lngStart = Math.floor((this._fallbackView.lng - this._fallbackView.spanLng / 2) / gridStep) * gridStep;
    const lngEnd = this._fallbackView.lng + this._fallbackView.spanLng / 2;
    for (let lng = lngStart; lng <= lngEnd; lng += gridStep) {
      const point = this._fallbackProject(lng, this._fallbackView.lat, width, height);
      context.beginPath(); context.moveTo(point.x, 0); context.lineTo(point.x, height); context.stroke();
    }
    const latStart = Math.floor((this._fallbackView.lat - this._fallbackView.spanLat / 2) / gridStep) * gridStep;
    const latEnd = this._fallbackView.lat + this._fallbackView.spanLat / 2;
    for (let lat = latStart; lat <= latEnd; lat += gridStep) {
      const point = this._fallbackProject(this._fallbackView.lng, lat, width, height);
      context.beginPath(); context.moveTo(0, point.y); context.lineTo(width, point.y); context.stroke();
    }

    if (this._fallbackView.spanLng > 25) {
      context.fillStyle = "#28332e";
      context.strokeStyle = "rgba(145,170,150,0.22)";
      FALLBACK_LANDMASSES.forEach((polygon) => {
        context.beginPath();
        polygon.forEach(([lng, lat], index) => {
          const point = this._fallbackProject(lng, lat, width, height);
          if (index === 0) context.moveTo(point.x, point.y); else context.lineTo(point.x, point.y);
        });
        context.closePath(); context.fill(); context.stroke();
      });
    }

    this.evidence.forEach((descriptor) => {
      if (this.layerState[descriptor.layer] === false) return;
      this._drawFallbackDescriptor(context, descriptor, width, height);
    });

    context.fillStyle = "rgba(220,225,218,0.58)";
    context.font = `${Math.max(10, Math.round(width / 180))}px ${getComputedStyle(document.documentElement).getPropertyValue("--font-mono") || "monospace"}`;
    context.fillText("本地空间数据 · 地图服务重连中", 16, 24);
  }

  _drawFallbackDescriptor(context, descriptor, width, height) {
    const point = normalizeTarget(descriptor.position || descriptor);
    const projected = this._fallbackProject(point.lng, point.lat, width, height);
    if (projected.x < -40 || projected.x > width + 40 || projected.y < -40 || projected.y > height + 40) return;
    const color = descriptor.color || COLOR_BY_LAYER[descriptor.layer] || COLOR_BY_LAYER.evidence;
    const highlighted = this.highlightedIds.has(descriptor.id);
    context.save();
    context.strokeStyle = color;
    context.fillStyle = color;
    context.lineWidth = highlighted ? 4 : 2;
    if (descriptor.type === "circle") {
      const degrees = (Number(descriptor.radius || 1000) / 111_320);
      const radius = Math.max(5, (degrees / this._fallbackView.spanLat) * height);
      context.globalAlpha = 0.18;
      context.beginPath(); context.arc(projected.x, projected.y, radius, 0, Math.PI * 2); context.fill();
      context.globalAlpha = 0.86;
      context.beginPath(); context.arc(projected.x, projected.y, radius, 0, Math.PI * 2); context.stroke();
    } else if (descriptor.type === "polygon" && Array.isArray(descriptor.coordinates)) {
      context.globalAlpha = 0.22;
      context.beginPath();
      descriptor.coordinates.forEach((coordinate, index) => {
        const p = this._fallbackProject(Number(coordinate[0]), Number(coordinate[1]), width, height);
        if (index === 0) context.moveTo(p.x, p.y); else context.lineTo(p.x, p.y);
      });
      context.closePath(); context.fill();
      context.globalAlpha = 0.9; context.stroke();
    } else {
      context.beginPath(); context.arc(projected.x, projected.y, highlighted ? 8 : 5, 0, Math.PI * 2); context.fill();
      context.strokeStyle = "rgba(11,15,14,0.85)"; context.lineWidth = 2; context.stroke();
      const showLabel = descriptor.label
        && this.quality !== "low"
        && (descriptor.layer === "parcel" || descriptor.layer === "searchArea" || highlighted);
      if (showLabel) {
        context.fillStyle = "rgba(242,244,239,0.9)";
        context.font = `${Math.max(10, Math.round(width / 180))}px sans-serif`;
        context.fillText(String(descriptor.label).slice(0, 18), projected.x + 10, projected.y + 4);
      }
    }
    context.restore();
  }

  async flyTo(target, preset = "site") {
    if (this.activeEngine === "amap" && this.amapMap) {
      const normalized = normalizeTarget(target?.gcj02 || target?.amapPosition || target);
      this.amapFocus = { key: `${normalized.lng.toFixed(6)},${normalized.lat.toFixed(6)}`, ...normalized };
      if (preset === "site") {
        this.amapCameraPrimed = false;
        this._frameAmapScene();
      } else {
        const camera = AMAP_PRESETS[preset] || AMAP_PRESETS.presentation;
        const pose = this.amapMode === "2d" ? { ...camera, pitch: 0, rotation: 0 } : camera;
        if (this.locaScene) {
          await this.locaScene.animateCamera([normalized.lng, normalized.lat], pose);
        } else {
          this.amapMap.setZoomAndCenter?.(pose.zoom, [normalized.lng, normalized.lat]);
          this.amapMap.setPitch?.(pose.pitch);
          this.amapMap.setRotation?.(pose.rotation);
        }
      }
      return { completed: true, engine: "amap" };
    }
    const normalized = normalizeTarget(target);
    const camera = CAMERA_PRESETS[preset] || CAMERA_PRESETS.site;
    const height = normalized.height || camera.height;
    const generation = ++this.flightGeneration;
    if (this.mode === "2d") {
      this._fallbackView.lng = normalized.lng;
      this._fallbackView.lat = normalized.lat;
      const spans = { globe: [360, 170], city: [2.8, 1.6], site: [0.12, 0.07], context: [0.48, 0.28], evidence: [1.2, 0.7], presentation: [0.24, 0.14] };
      [this._fallbackView.spanLng, this._fallbackView.spanLat] = spans[preset] || spans.site;
      this._drawFallback();
      return { completed: true, mode: "2d" };
    }
    if (!this.viewer || this.destroyed) return { completed: false, reason: "scene_unavailable" };

    const Cesium = globalThis.Cesium;
    const duration = this.config.reducedMotion ? 0 : (QUALITY_PROFILES[this.quality]?.cameraDuration || 1.8);
    const destination = Cesium.Cartesian3.fromDegrees(normalized.lng, normalized.lat, height);
    const orientation = {
      heading: Cesium.Math.toRadians(Number(camera.heading || 0)),
      pitch: Cesium.Math.toRadians(Number(camera.pitch || -50)),
      roll: 0,
    };
    if (duration === 0) {
      this.viewer.camera.setView({ destination, orientation });
      this.viewer.scene.requestRender();
      return { completed: true, mode: "3d" };
    }
    return new Promise((resolve) => {
      this.viewer.camera.flyTo({
        destination,
        orientation,
        duration,
        easingFunction: Cesium.EasingFunction.CUBIC_IN_OUT,
        complete: () => resolve({ completed: generation === this.flightGeneration, mode: "3d" }),
        cancel: () => resolve({ completed: false, cancelled: true, mode: "3d" }),
      });
    });
  }

  pick(screenPosition) {
    if (this.activeEngine === "amap" && this.amapMap && this.amapContainer) {
      const point = screenPoint(screenPosition, this.amapContainer);
      if (!point) return null;
      const pixel = globalThis.AMap?.Pixel ? new globalThis.AMap.Pixel(point.x, point.y) : [point.x, point.y];
      const lnglat = this.amapMap.containerToLngLat?.(pixel);
      const coordinates = lnglat?.toArray?.() || (lnglat && [lnglat.getLng?.(), lnglat.getLat?.()]);
      if (Array.isArray(coordinates) && coordinates.every(Number.isFinite)) {
        return { lng: coordinates[0], lat: coordinates[1], height: 0, source: `amap-${this.amapMode}` };
      }
      return null;
    }
    const canvas = this.mode === "2d" ? this.fallbackCanvas : this.viewer?.scene?.canvas;
    const point = screenPoint(screenPosition, canvas);
    if (!point || !canvas) return null;
    if (this.mode === "2d") {
      const rect = canvas.getBoundingClientRect();
      const lng = this._fallbackView.lng - this._fallbackView.spanLng / 2 + (point.x / rect.width) * this._fallbackView.spanLng;
      const lat = this._fallbackView.lat + this._fallbackView.spanLat / 2 - (point.y / rect.height) * this._fallbackView.spanLat;
      return { lng: clamp(lng, -180, 180), lat: clamp(lat, -90, 90), height: 0, source: "2d" };
    }
    if (!this.viewer) return null;
    const Cesium = globalThis.Cesium;
    const cartesian2 = new Cesium.Cartesian2(point.x, point.y);
    let cartesian;
    if (this.viewer.scene.pickPositionSupported) cartesian = this.viewer.scene.pickPosition(cartesian2);
    if (!Cesium.defined(cartesian)) {
      const ray = this.viewer.camera.getPickRay(cartesian2);
      cartesian = ray && this.viewer.scene.globe.pick(ray, this.viewer.scene);
    }
    if (!Cesium.defined(cartesian)) cartesian = this.viewer.camera.pickEllipsoid(cartesian2, this.viewer.scene.globe.ellipsoid);
    if (!Cesium.defined(cartesian)) return null;
    const cartographic = Cesium.Cartographic.fromCartesian(cartesian);
    return {
      lng: Cesium.Math.toDegrees(cartographic.longitude),
      lat: Cesium.Math.toDegrees(cartographic.latitude),
      height: Math.max(0, cartographic.height || 0),
      source: "3d",
    };
  }

  setLayers(layerState = {}) {
    const evidenceChanged = Array.isArray(layerState.evidence);
    if (evidenceChanged) {
      this.evidence = layerState.evidence.filter((item) => item?.id && item?.position);
      this._reconcileCesiumEvidence();
    }
    this.layerState = { ...this.layerState, ...publicLayerState(layerState) };
    if (this.viewer) {
      this.evidence.forEach((descriptor) => {
        const entity = this.viewer.entities.getById(descriptor.id);
        if (entity) entity.show = this.layerState[descriptor.layer] !== false;
      });
      this.tilesets.forEach((tileset) => { tileset.show = this.layerState.buildings !== false; });
      this.viewer.scene.requestRender();
    }
    if (this.mode === "2d") this._drawFallback();
    if (this.amapMap) {
      if (evidenceChanged) {
        this._reconcileAmapEvidence();
      } else {
        this.locaScene?.setVisibility(this.layerState, this.highlightedIds);
        this.amapDecisionLayers?.setVisibility(this.layerState);
        this.evidence.forEach((descriptor) => {
          const overlay = this.amapOverlays.get(String(descriptor.id));
          if (!overlay) return;
          if (this.layerState[descriptor.layer] === false) overlay.hide?.();
          else overlay.show?.();
        });
      }
    }
    return { ...this.layerState };
  }

  _reconcileAmapEvidence() {
    if (!this.amapMap || !globalThis.AMap) return;
    this.amapOverlays.forEach((overlay) => this.amapMap.remove(overlay));
    this.amapOverlays.clear();
    this.evidence.forEach((descriptor) => {
      if (this.layerState[descriptor.layer] === false) return;
      if (descriptor.type === "point" && descriptor.layer === "competitors") return;
      if (this.locaScene && descriptor.type === "point" && ["resources", "risks"].includes(descriptor.layer)) return;
      try {
        const overlay = this._addAmapDescriptor(descriptor);
        if (overlay) {
          this.amapMap.add(overlay);
          this.amapOverlays.set(String(descriptor.id), overlay);
        }
      } catch (error) {
        this._emitStatus({ mode: `amap-${this.amapMode}`, label: "部分 DDS 图层未渲染", tone: "warning", reason: error.message });
      }
    });
    this.amapDecisionLayers?.setEvidence(this.evidence, this.layerState);
    this.locaScene?.setEvidence(this.evidence, this.layerState, this.highlightedIds);
    this._frameAmapScene();
  }

  _frameAmapScene() {
    if (!this.amapMap || this.amapCameraPrimed) return;
    this.amapCameraPrimed = true;
    const host = this.container?.parentElement || this.container;
    const narrow = Number(host?.clientWidth || globalThis.innerWidth || 0) < 768;
    const avoid = narrow ? [76, 28, 238, 28] : [88, 104, 278, 104];
    const parcelOverlays = this.evidence
      .filter((item) => item.layer === "parcel" && (item.type === "circle" || item.type === "polygon"))
      .map((item) => this.amapOverlays.get(String(item.id)))
      .filter(Boolean);
    if (parcelOverlays.length && typeof this.amapMap.setFitView === "function") {
      this.amapMap.setFitView(parcelOverlays, false, avoid, 15);
    } else if (this.amapFocus) {
      this.amapMap.setZoomAndCenter(AMAP_CAMERA.zoom, [this.amapFocus.lng, this.amapFocus.lat]);
    }
    const applyPose = () => {
      this.amapMap?.setPitch?.(this.amapMode === "3d" ? AMAP_CAMERA.pitch : 0);
      this.amapMap?.setRotation?.(this.amapMode === "3d" ? AMAP_CAMERA.rotation : 0);
    };
    applyPose();
    globalThis.requestAnimationFrame?.(applyPose);
  }

  _addAmapDescriptor(descriptor) {
    const AMap = globalThis.AMap;
    const point = normalizeTarget(descriptor.amapPosition || descriptor.position || descriptor);
    const center = [point.lng, point.lat];
    const color = descriptor.color || COLOR_BY_LAYER[descriptor.layer] || COLOR_BY_LAYER.evidence;
    if (descriptor.type === "circle") {
      const isParcelField = descriptor.layer === "parcel";
      const isSearchArea = descriptor.layer === "searchArea";
      return new AMap.Circle({
        center,
        radius: Number(descriptor.radius || 1000),
        strokeColor: color,
        strokeWeight: isParcelField ? 4 : isSearchArea ? 1.5 : 2,
        strokeStyle: isSearchArea ? "dashed" : "solid",
        strokeOpacity: isParcelField ? 0.96 : isSearchArea ? 0.62 : 0.82,
        fillColor: color,
        fillOpacity: isParcelField ? 0.14 : isSearchArea ? 0.025 : 0.08,
        zIndex: isParcelField ? 34 : 20,
        bubble: true,
      });
    }
    if (descriptor.type === "polygon" && Array.isArray(descriptor.amapCoordinates || descriptor.coordinates)) {
      return new AMap.Polygon({
        path: descriptor.amapCoordinates || descriptor.coordinates,
        strokeColor: color,
        strokeWeight: 3,
        strokeOpacity: 0.96,
        fillColor: color,
        fillOpacity: 0.2,
        zIndex: 36,
        bubble: true,
      });
    }
    const content = document.createElement("button");
    content.type = "button";
    content.className = `dds-map-marker dds-map-marker--${descriptor.layer}${this.highlightedIds.has(String(descriptor.id)) ? " is-highlighted" : ""}`;
    content.style.setProperty("--marker-color", color);
    content.setAttribute("aria-label", descriptor.label || descriptor.id);
    const dot = document.createElement("span");
    dot.className = "dds-map-marker__dot";
    content.appendChild(dot);
    if (descriptor.layer === "parcel") {
      const label = document.createElement("span");
      label.className = "dds-map-marker__label";
      label.textContent = String(descriptor.label || "目标地块").slice(0, 24);
      content.appendChild(label);
    }
    const marker = new AMap.Marker({
      position: center,
      content,
      anchor: "center",
      zIndex: ({ parcel: 160, risks: 150, competitors: 140, resources: 130 })[descriptor.layer] || 60,
      zooms: descriptor.layer === "resources" ? [14, 20] : descriptor.layer === "competitors" ? [12, 20] : [3, 20],
      title: descriptor.label || descriptor.id,
    });
    marker.on("click", () => { this._openAmapDescriptor(descriptor, center); this.onFeatureSelected?.(String(descriptor.id)); });
    return marker;
  }

  _openAmapDescriptor(descriptor, position) {
    const AMap = globalThis.AMap;
    if (!AMap || !this.amapMap) return;
    const panel = document.createElement("div");
    panel.className = "dds-map-popover";
    const eyebrow = document.createElement("span");
    eyebrow.textContent = ({ parcel: "目标地块", competitors: "竞品", resources: "城市资源", risks: "风险 / 缺口" })[descriptor.layer] || "DDS 证据";
    const title = document.createElement("strong");
    title.textContent = descriptor.label || descriptor.id;
    panel.append(eyebrow, title);
    const data = descriptor.data || {};
    const details = [
      data.price || data.unit_price || data.unit_price_cny || data.avg_price,
      data.distance_m || data.distance,
      data.category || data.type,
      data.status || data.state,
    ].filter((value) => value !== undefined && value !== null && value !== "").slice(0, 3);
    if (details.length) {
      const meta = document.createElement("small");
      meta.textContent = details.join(" · ");
      panel.appendChild(meta);
    }
    this.amapInfoWindow?.close?.();
    this.amapInfoWindow = new AMap.InfoWindow({ isCustom: true, content: panel, offset: new AMap.Pixel(0, -18) });
    this.amapInfoWindow.open(this.amapMap, position);
  }
  _reconcileCesiumEvidence() {
    if (!this.viewer || this.mode !== "3d") return;
    this.evidenceIds.forEach((id) => this.viewer.entities.removeById(id));
    this.evidenceIds.clear();
    this.evidence.forEach((descriptor) => {
      try {
        this._addCesiumDescriptor(descriptor);
        this.evidenceIds.add(descriptor.id);
      } catch (error) {
        this._emitStatus({ mode: "3d", label: "部分证据未渲染", tone: "warning", reason: error.message });
      }
    });
  }

  _addCesiumDescriptor(descriptor) {
    const Cesium = globalThis.Cesium;
    const point = normalizeTarget(descriptor.position || descriptor);
    const color = Cesium.Color.fromCssColorString(descriptor.color || COLOR_BY_LAYER[descriptor.layer] || COLOR_BY_LAYER.evidence);
    const common = {
      id: descriptor.id,
      name: descriptor.label || descriptor.id,
      show: this.layerState[descriptor.layer] !== false,
      properties: { layerId: descriptor.layer, source: descriptor.source || "dds" },
      position: Cesium.Cartesian3.fromDegrees(point.lng, point.lat, Number(point.height || 0)),
    };
    if (descriptor.type === "circle") {
      return this.viewer.entities.add({
        ...common,
        ellipse: {
          semiMajorAxis: Number(descriptor.radius || 1000),
          semiMinorAxis: Number(descriptor.radius || 1000),
          material: color.withAlpha(0.16),
          outline: true,
          outlineColor: color.withAlpha(0.9),
          heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
        },
      });
    }
    if (descriptor.type === "polygon" && Array.isArray(descriptor.coordinates)) {
      const degrees = descriptor.coordinates.flatMap(([lng, lat]) => [Number(lng), Number(lat)]);
      return this.viewer.entities.add({
        ...common,
        position: undefined,
        polygon: {
          hierarchy: Cesium.Cartesian3.fromDegreesArray(degrees),
          material: color.withAlpha(0.2),
          outline: true,
          outlineColor: color,
          heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
        },
      });
    }
    return this.viewer.entities.add({
      ...common,
      point: {
        pixelSize: Number(descriptor.size || 9),
        color,
        outlineColor: Cesium.Color.fromCssColorString("#0b0f0e"),
        outlineWidth: 2,
        disableDepthTestDistance: 24_000,
        heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
      },
      label: descriptor.label && this.quality !== "low" ? {
        text: String(descriptor.label).slice(0, 24),
        font: "12px sans-serif",
        fillColor: Cesium.Color.fromCssColorString("#f2f4ef"),
        showBackground: true,
        backgroundColor: Cesium.Color.fromCssColorString("#0b0f0e").withAlpha(0.76),
        pixelOffset: new Cesium.Cartesian2(0, -18),
        distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, this.quality === "high" ? 50_000 : 24_000),
        disableDepthTestDistance: 24_000,
      } : undefined,
    });
  }

  highlight(entityIds = []) {
    this.highlightedIds = new Set((entityIds || []).map(String));
    if (this.viewer) {
      this.evidence.forEach((descriptor) => {
        const entity = this.viewer.entities.getById(descriptor.id);
        if (!entity?.point) return;
        const active = this.highlightedIds.has(String(descriptor.id));
        entity.point.pixelSize = active ? 16 : Number(descriptor.size || 9);
        entity.point.outlineWidth = active ? 4 : 2;
      });
      this.viewer.scene.requestRender();
    }
    if (this.mode === "2d") this._drawFallback();
    if (this.amapMap) this.locaScene?.setEvidence(this.evidence, this.layerState, this.highlightedIds);
  }

  setAmapBasemap(mode = "color") {
    return this.amapDecisionLayers?.setBasemap(mode) || "color";
  }

  setAmapTheme(theme = "dds") {
    return this.amapDecisionLayers?.setTheme(theme) || "dds";
  }

  searchNearby(category, options = {}) {
    if (!this.amapDecisionLayers) return Promise.reject(new Error("高德地图尚未就绪"));
    return this.amapDecisionLayers.searchNearby(category, options);
  }

  getAmapSiteContext(options = {}) {
    return this.amapDecisionLayers?.getSiteContext(options) || Promise.reject(new Error("高德地图尚未就绪"));
  }

  setAmapLiveVisibility(visible) {
    this.amapDecisionLayers?.setLiveContextVisible(visible);
  }

  startMeasure(type) {
    if (!this.amapDecisionLayers) return Promise.reject(new Error("高德地图尚未就绪"));
    return this.amapDecisionLayers.startMeasure(type);
  }

  clearMeasure() {
    this.amapDecisionLayers?.clearMeasure();
  }

  focusPoi(id) {
    return this.amapDecisionLayers?.focusPoi(id) || false;
  }

  routeToPoi(id, mode = "walking") {
    return this.amapDecisionLayers?.routeToPoi(id, mode) || Promise.reject(new Error("高德地图尚未就绪"));
  }

  captureBookmark(name) {
    if (this.activeEngine === "amap" && this.amapMap) {
      const center = this.amapMap.getCenter?.();
      const coordinates = center?.toArray?.() || [center?.getLng?.(), center?.getLat?.()];
      return {
        name,
        mode: `amap-${this.amapMode}`,
        lng: Number(coordinates?.[0]),
        lat: Number(coordinates?.[1]),
        zoom: Number(this.amapMap.getZoom?.() || 14),
        heading: Number(this.amapMap.getRotation?.() || 0),
        pitch: Number(this.amapMap.getPitch?.() || 0),
        basemap: this.amapDecisionLayers?.basemap || "color",
        layers: publicLayerState(this.layerState),
      };
    }
    if (this.mode === "2d") {
      return {
        name,
        mode: "2d",
        lng: this._fallbackView.lng,
        lat: this._fallbackView.lat,
        height: this._fallbackView.spanLng,
        heading: 0,
        pitch: -90,
        roll: 0,
        layers: publicLayerState(this.layerState),
      };
    }
    if (!this.viewer) return null;
    const Cesium = globalThis.Cesium;
    const position = this.viewer.camera.positionCartographic;
    return {
      name,
      mode: "3d",
      lng: Cesium.Math.toDegrees(position.longitude),
      lat: Cesium.Math.toDegrees(position.latitude),
      height: position.height,
      heading: Cesium.Math.toDegrees(this.viewer.camera.heading),
      pitch: Cesium.Math.toDegrees(this.viewer.camera.pitch),
      roll: Cesium.Math.toDegrees(this.viewer.camera.roll),
      layers: publicLayerState(this.layerState),
    };
  }

  restoreBookmark(bookmark) {
    if (!bookmark) return Promise.resolve({ completed: false });
    this.setLayers(bookmark.layers || {});
    if (this.activeEngine === "amap" && this.amapMap) {
      this.setAmapMode(String(bookmark.mode || "").includes("3d") ? "3d" : this.amapMode, { animate: false });
      if (bookmark.basemap) this.setAmapBasemap(bookmark.basemap);
      this.amapMap.setZoomAndCenter?.(Number(bookmark.zoom || 14), [Number(bookmark.lng), Number(bookmark.lat)]);
      this.amapMap.setPitch?.(Number(bookmark.pitch || 0));
      this.amapMap.setRotation?.(Number(bookmark.heading || 0));
      return Promise.resolve({ completed: true, mode: `amap-${this.amapMode}` });
    }
    if (this.mode === "2d") {
      this._fallbackView.lng = Number(bookmark.lng || 0);
      this._fallbackView.lat = Number(bookmark.lat || 0);
      this._fallbackView.spanLng = clamp(Number(bookmark.height || 1), 0.02, 360);
      this._fallbackView.spanLat = clamp(this._fallbackView.spanLng * 0.56, 0.01, 170);
      this._drawFallback();
      return Promise.resolve({ completed: true, mode: "2d" });
    }
    const Cesium = globalThis.Cesium;
    const duration = this.config.reducedMotion ? 0 : (QUALITY_PROFILES[this.quality]?.cameraDuration || 1.4);
    const options = {
      destination: Cesium.Cartesian3.fromDegrees(Number(bookmark.lng), Number(bookmark.lat), Number(bookmark.height || 2400)),
      orientation: {
        heading: Cesium.Math.toRadians(Number(bookmark.heading || 0)),
        pitch: Cesium.Math.toRadians(Number(bookmark.pitch ?? -50)),
        roll: Cesium.Math.toRadians(Number(bookmark.roll || 0)),
      },
      duration,
    };
    if (duration === 0) {
      this.viewer.camera.setView(options);
      return Promise.resolve({ completed: true, mode: "3d" });
    }
    return new Promise((resolve) => this.viewer.camera.flyTo({
      ...options,
      complete: () => resolve({ completed: true, mode: "3d" }),
      cancel: () => resolve({ completed: false, cancelled: true, mode: "3d" }),
    }));
  }

  async playTour(bookmarks = []) {
    const generation = ++this.flightGeneration;
    for (const bookmark of bookmarks) {
      if (generation !== this.flightGeneration) return { completed: false, cancelled: true };
      const result = await this.restoreBookmark(bookmark);
      if (!result?.completed) return result;
    }
    return { completed: true };
  }

  setQuality(profile = "balanced") {
    const name = QUALITY_PROFILES[profile] ? profile : "balanced";
    const settings = QUALITY_PROFILES[name];
    this.quality = name;
    if (this.viewer) {
      const ratio = Math.min(globalThis.devicePixelRatio || 1, 2);
      this.viewer.resolutionScale = clamp(settings.resolutionScale / Math.max(1, ratio / 1.5), 0.55, 1);
      this.viewer.shadows = settings.shadows;
      this.viewer.scene.globe.maximumScreenSpaceError = settings.maximumScreenSpaceError;
      this.viewer.scene.fog.enabled = name !== "low";
      this.viewer.scene.globe.showGroundAtmosphere = name !== "low";
      this.viewer.scene.requestRender();
      this._reconcileCesiumEvidence();
    }
    if (this.mode === "2d") this._drawFallback();
    this.locaScene?.setQuality(name);
    const sceneLabel = this.activeEngine === "amap"
      ? `高德 ${this.amapMode.toUpperCase()}`
      : this.mode === "2d" ? "2D" : this._isBaseScene() ? "基础场景" : "3D";
    this._emitStatus({ mode: this.mode, label: `${name.toUpperCase()} / ${sceneLabel}`, tone: "ok", quality: name });
    return name;
  }

  cancelFlight() {
    this.flightGeneration += 1;
    this.viewer?.camera?.cancelFlight?.();
    this.locaScene?.cancelCamera();
  }

  destroy() {
    this.destroyed = true;
    this.cancelFlight();
    this._resizeObserver?.disconnect();
    this._resizeObserver = null;
    this._unbindFallbackEvents();
    this.amapInfoWindow?.close?.();
    this.amapOverlays.forEach((overlay) => this.amapMap?.remove?.(overlay));
    this.amapOverlays.clear();
    this.amapDecisionLayers?.destroy();
    this.amapDecisionLayers = null;
    this.locaScene?.destroy();
    this.locaScene = null;
    this.amapMap?.destroy?.();
    this.amapMap = null;
    this.amapContainer = null;
    this.amapReady = null;
    this.locaReady = null;
    this._contextListeners.forEach(([target, type, listener]) => target.removeEventListener(type, listener));
    this._contextListeners = [];
    if (this.viewer && !this.viewer.isDestroyed?.()) this.viewer.destroy();
    this.viewer = null;
    this.mode = "destroyed";
  }

  _emitStatus(status) {
    this.onStatus?.(status);
  }
}
