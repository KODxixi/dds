const POI_CATEGORIES = Object.freeze({
  transit: Object.freeze({ label: "交通", keyword: "地铁站|公交站", type: "交通设施服务", color: "#5f9fbd" }),
  education: Object.freeze({ label: "教育", keyword: "学校", type: "科教文化服务", color: "#7f9f63" }),
  medical: Object.freeze({ label: "医疗", keyword: "医院", type: "医疗保健服务", color: "#c66f67" }),
  retail: Object.freeze({ label: "商业", keyword: "商场", type: "购物服务", color: "#c7954d" }),
  park: Object.freeze({ label: "公园", keyword: "公园", type: "风景名胜|公园广场", color: "#5e9b83" }),
});

const BUILDING_STYLES = Object.freeze({
  highrise: Object.freeze({ label: "高层住宅", roof: "ff79a7c5", wall: "ff31566f", accent: "#79a7c5" }),
  midrise: Object.freeze({ label: "小高层", roof: "ff8fb594", wall: "ff456e4d", accent: "#8fb594" }),
  lowrise: Object.freeze({ label: "低密住区", roof: "ffd5ad68", wall: "ff7a5a2f", accent: "#d5ad68" }),
  commercial: Object.freeze({ label: "商业办公", roof: "ffc6819d", wall: "ff6d3f55", accent: "#c6819d" }),
  mixed: Object.freeze({ label: "混合/待核", roof: "ff9a9f9c", wall: "ff505754", accent: "#9a9f9c" }),
});

function pointOf(value) {
  const source = value?.amapPosition || value?.gcj02 || value?.position || value;
  const lng = Number(source?.lng ?? source?.lon ?? source?.longitude ?? source?.[0]);
  const lat = Number(source?.lat ?? source?.latitude ?? source?.[1]);
  if (!Number.isFinite(lng) || !Number.isFinite(lat)) return null;
  if (lng < -180 || lng > 180 || lat < -90 || lat > 90) return null;
  return [lng, lat];
}

function pluginAvailable(name) {
  return Boolean(globalThis.AMap?.[String(name).replace(/^AMap\./, "")]);
}

function loadPlugins(names, timeout = 10000) {
  const AMap = globalThis.AMap;
  if (!AMap?.plugin) return Promise.resolve([]);
  const pending = [...new Set(names)].filter((name) => !pluginAvailable(name));
  if (!pending.length) return Promise.resolve(names);
  return new Promise((resolve) => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(names.filter(pluginAvailable));
    };
    const timer = globalThis.setTimeout(finish, timeout);
    try {
      AMap.plugin(pending, finish);
    } catch {
      finish();
    }
  });
}

function escapeXml(value) {
  return String(value || "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character]);
}

function markerIcon(color) {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><circle cx="12" cy="12" r="8" fill="${escapeXml(color)}" stroke="#f2f4ef" stroke-opacity=".82" stroke-width="2"/><circle cx="12" cy="12" r="2.5" fill="#101412"/></svg>`;
  return `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`;
}

function buildingClass(data = {}) {
  const description = [data.building_type, data.buildingType, data.property_type, data.propertyType, data.category]
    .filter(Boolean)
    .join(" ");
  if (/商业|写字楼|办公|商办|产业|公寓/.test(description)) return "commercial";
  if (/别墅|洋房|叠拼|合院|低层|低密/.test(description)) return "lowrise";
  if (/小高层/.test(description)) return "midrise";
  if (/高层|超高层/.test(description)) return "highrise";
  return "mixed";
}

function polygonOf(data = {}) {
  const geometry = data.geometry || data.boundary || data.polygon || data.footprint;
  const coordinates = geometry?.type === "Polygon" ? geometry.coordinates?.[0] : geometry?.coordinates || geometry;
  if (!Array.isArray(coordinates) || coordinates.length < 3) return null;
  const path = coordinates.map((point) => pointOf(point)).filter(Boolean);
  return path.length >= 3 ? path : null;
}

function formatPrice(value) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0
    ? `${new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(number)} 元/㎡`
    : "价格待核";
}

function poiPosition(poi) {
  if (Array.isArray(poi?.location)) return pointOf(poi.location);
  if (typeof poi?.location?.toArray === "function") return pointOf(poi.location.toArray());
  if (poi?.location && typeof poi.location.getLng === "function") {
    return pointOf([poi.location.getLng(), poi.location.getLat()]);
  }
  return pointOf(poi);
}

export class AmapDecisionLayers {
  constructor({ map, mapStyle = "", onSelect = null, onPoiUpdate = null, onStatus = null } = {}) {
    if (!map) throw new TypeError("AmapDecisionLayers requires an AMap instance");
    this.map = map;
    this.onSelect = onSelect;
    this.onPoiUpdate = onPoiUpdate;
    this.onStatus = onStatus;
    this.mode = "2d";
    this.basemap = "color";
    this.theme = "dds";
    this.customMapStyle = mapStyle || "";
    this.layerState = {};
    this.descriptors = [];
    this.competitorCluster = null;
    this.competitorMarkers = [];
    this.clusterEngine = "none";
    this.poiMarkers = new Map();
    this.poiResults = [];
    this.poiCategory = null;
    this.poiSearchGeneration = 0;
    this.poiInfoWindow = null;
    this.mouseTool = null;
    this.scaleControl = null;
    this.buildingZones = [];
    this.districtOverlays = [];
    this.liveContextVisible = false;
    this.routeService = null;
    this.destroyed = false;
    this._clusterVisible = true;
    this._poiVisible = true;
  }

  initialize() {
    const AMap = globalThis.AMap;
    this.vectorLayer = AMap.createDefaultLayer?.({ zooms: [3, 20] }) || null;
    this.satelliteLayer = AMap.TileLayer?.Satellite ? new AMap.TileLayer.Satellite({ zooms: [3, 20] }) : null;
    this.roadNetLayer = AMap.TileLayer?.RoadNet ? new AMap.TileLayer.RoadNet({ zooms: [3, 20] }) : null;
    this.buildingLayer = AMap.Buildings ? new AMap.Buildings({ zIndex: 130, zooms: [15, 20] }) : null;
    this.trafficLayer = AMap.TileLayer?.Traffic ? new AMap.TileLayer.Traffic({ zooms: [7, 20], autoRefresh: true, interval: 180 }) : null;
    this.setBasemap("color");
    this.pluginsReady = loadPlugins([
      "AMap.IndexCluster",
      "AMap.MarkerCluster",
      "AMap.PlaceSearch",
      "AMap.MouseTool",
      "AMap.Scale",
      "AMap.Geocoder",
      "AMap.DistrictSearch",
      "AMap.Weather",
    ]).then(() => {
      if (this.destroyed) return;
      if (AMap.Scale && !this.scaleControl) {
        this.scaleControl = new AMap.Scale();
        this.map.addControl?.(this.scaleControl);
      }
      this._refreshCompetitors();
    });
    return this;
  }

  setEvidence(descriptors = [], layerState = {}) {
    this.descriptors = descriptors.filter((item) => item?.id);
    this.layerState = { ...this.layerState, ...layerState };
    this._applyBuildingStyles();
    this._refreshCompetitors();
    this.setVisibility(this.layerState);
  }

  setVisibility(layerState = {}) {
    this.layerState = { ...this.layerState, ...layerState };
    this._setClusterVisible(this.layerState.competitors !== false);
    this._setPoiVisible(this.layerState.resources !== false);
    this._syncBaseLayers();
  }

  setMode(mode = "2d") {
    this.mode = mode === "3d" ? "3d" : "2d";
    this._syncBaseLayers();
  }

  setBasemap(mode = "color") {
    this.basemap = mode === "satellite" ? "satellite" : "color";
    this._syncBaseLayers();
    return this.basemap;
  }

  setTheme(theme = "dds") {
    this.theme = ["dds", "grey", "normal"].includes(theme) ? theme : "dds";
    if (this.basemap === "satellite") this.basemap = "color";
    this._syncBaseLayers();
    return this.theme;
  }

  _syncBaseLayers() {
    if (!this.map || this.destroyed) return;
    const useBuildings = this.mode === "3d" && this.layerState.buildings !== false && this.buildingLayer;
    let layers;
    if (this.basemap === "satellite" && this.satelliteLayer) {
      layers = [this.satelliteLayer, this.roadNetLayer, useBuildings ? this.buildingLayer : null, this.liveContextVisible ? this.trafficLayer : null].filter(Boolean);
    } else {
      layers = [this.vectorLayer, useBuildings ? this.buildingLayer : null, this.liveContextVisible ? this.trafficLayer : null].filter(Boolean);
    }
    if (layers.length) this.map.setLayers?.(layers);
    if (this.basemap !== "satellite") {
      const styles = {
        dds: this.customMapStyle || "amap://styles/dark",
        grey: "amap://styles/grey",
        normal: "amap://styles/normal",
      };
      this.map.setMapStyle?.(styles[this.theme] || styles.dds);
    }
    this.map.setFeatures?.(this.mode === "3d"
      ? ["bg", "road", "point", "building"]
      : ["bg", "road", "point"]);
    if (useBuildings) this.buildingLayer.show?.();
    else this.buildingLayer?.hide?.();
    this.buildingZones.forEach((zone) => useBuildings ? zone.show?.() : zone.hide?.());
    this.districtOverlays.forEach((overlay) => this.liveContextVisible ? overlay.show?.() : overlay.hide?.());
  }

  setLiveContextVisible(visible) {
    this.liveContextVisible = Boolean(visible);
    this._syncBaseLayers();
  }

  async getSiteContext({ center = null, city = "", district = "" } = {}) {
    const site = pointOf(center) || pointOf(this.descriptors.find((item) => item.id === "parcel-anchor"));
    if (!site) throw new Error("缺少高德实况查询坐标");
    await loadPlugins(["AMap.Geocoder", "AMap.DistrictSearch", "AMap.Weather"]);
    if (this.destroyed || !this.map) return null;

    const geocode = await new Promise((resolve) => {
      if (!globalThis.AMap?.Geocoder) return resolve(null);
      new globalThis.AMap.Geocoder({ radius: 1000, extensions: "all" }).getAddress(site, (status, result) => {
        resolve(status === "complete" ? result?.regeocode || null : null);
      });
    });
    const component = geocode?.addressComponent || {};
    const districtName = district || component.district || "";
    const weatherTarget = component.adcode || city || component.city || component.province || districtName;
    const weather = await new Promise((resolve) => {
      if (!globalThis.AMap?.Weather || !weatherTarget) return resolve(null);
      new globalThis.AMap.Weather().getLive(weatherTarget, (error, data) => resolve(error ? null : data));
    });

    if (this.districtOverlays.length) this.map.remove?.(this.districtOverlays);
    this.districtOverlays = [];
    if (globalThis.AMap?.DistrictSearch && districtName) {
      const boundaries = await new Promise((resolve) => {
        const search = new globalThis.AMap.DistrictSearch({ level: "district", subdistrict: 0, extensions: "all" });
        search.search(districtName, (status, result) => resolve(status === "complete" ? result?.districtList?.[0]?.boundaries || [] : []));
      });
      this.districtOverlays = boundaries.map((path) => new globalThis.AMap.Polygon({
        path,
        zIndex: 118,
        strokeColor: "#d17c62",
        strokeOpacity: 0.86,
        strokeWeight: 2,
        strokeStyle: "dashed",
        fillColor: "#d17c62",
        fillOpacity: 0.035,
      }));
      if (this.districtOverlays.length) this.map.add?.(this.districtOverlays);
    }
    this.setLiveContextVisible(true);
    return {
      source: "AMap JS API 2.0",
      formattedAddress: geocode?.formattedAddress || "地址待核",
      province: component.province || "",
      city: component.city || city || "",
      district: districtName,
      township: component.township || "",
      adcode: component.adcode || weather?.adcode || "",
      weather: weather ? {
        condition: weather.weather,
        temperature: weather.temperature,
        humidity: weather.humidity,
        windDirection: weather.windDirection,
        windPower: weather.windPower,
        reportTime: weather.reportTime,
      } : null,
      traffic: Boolean(this.trafficLayer),
      boundaryCount: this.districtOverlays.length,
    };
  }

  _applyBuildingStyles() {
    if (!this.buildingLayer) return;
    if (this.buildingZones.length) this.map?.remove?.(this.buildingZones);
    this.buildingZones = [];
    if (this.districtOverlays.length) this.map?.remove?.(this.districtOverlays);
    this.districtOverlays = [];
    const competitors = this.descriptors
      .filter((item) => item.layer === "competitors" && item.type === "point" && pointOf(item))
      .sort((a, b) => Number(a.data?.distance_km ?? Infinity) - Number(b.data?.distance_km ?? Infinity))
      .slice(0, 12);
    const areas = competitors.map((descriptor) => {
      const type = buildingClass(descriptor.data);
      const style = BUILDING_STYLES[type];
      const path = polygonOf(descriptor.data);
      if (!path) return null;
      if (globalThis.AMap?.Polygon) {
        const zone = new globalThis.AMap.Polygon({
          path,
          zIndex: 125,
          zooms: [15, 20],
          strokeColor: style.accent,
          strokeOpacity: 0.82,
          strokeWeight: 1.5,
          strokeStyle: "dashed",
          fillColor: style.accent,
          fillOpacity: 0.07,
          bubble: true,
        });
        zone.on?.("click", () => this.onSelect?.(descriptor));
        this.buildingZones.push(zone);
      }
      return {
        rejectTexture: true,
        color1: style.roof,
        color2: style.wall,
        path,
      };
    }).filter(Boolean);
    this.buildingLayer.setStyle?.({ hideWithoutStyle: false, areas });
    if (this.buildingZones.length) this.map?.add?.(this.buildingZones);
    this._syncBaseLayers();
  }

  _clusterPoints() {
    return this.descriptors
      .filter((item) => item.layer === "competitors" && item.type === "point")
      .map((descriptor) => {
        const lnglat = pointOf(descriptor);
        if (!lnglat) return null;
        const data = descriptor.data || {};
        const rawWeight = Number(data.unit_price_cny ?? data.price ?? 1);
        return {
          lnglat,
          weight: Number.isFinite(rawWeight) ? Math.max(1, rawWeight) : 1,
          city: data.city || "项目城市",
          district: data.district || "区县待核",
          area: data.sub_district || data.area || data.block || "项目周边",
          community: data.community || data.sub_district || data.area || "项目周边",
          project: descriptor.label || descriptor.id,
          descriptorId: String(descriptor.id),
          descriptor,
        };
      })
      .filter(Boolean);
  }

  _refreshCompetitors() {
    if (!this.map || this.destroyed) return;
    this._clearCompetitors();
    const points = this._clusterPoints();
    if (!points.length || this.layerState.competitors === false) return;
    const AMap = globalThis.AMap;
    try {
      if (AMap?.IndexCluster) {
        const clusterIndexSet = {
          city: { minZoom: 3, maxZoom: 10 },
          district: { minZoom: 10, maxZoom: 12 },
          area: { minZoom: 12, maxZoom: 14.5 },
          community: { minZoom: 14.5, maxZoom: 16 },
          project: { minZoom: 16, maxZoom: 22 },
        };
        this.competitorCluster = new AMap.IndexCluster(this.map, points, {
          clusterIndexSet,
          renderClusterMarker: (context) => this._renderIndexCluster(context),
        });
        this.clusterEngine = "index";
        return;
      }
      if (AMap?.MarkerCluster) {
        this.competitorCluster = new AMap.MarkerCluster(this.map, points, {
          gridSize: 72,
          maxZoom: 18,
          renderClusterMarker: (context) => this._renderSpatialCluster(context),
          renderMarker: (context) => this._renderSpatialMarker(context),
        });
        this.clusterEngine = "spatial";
        return;
      }
    } catch (error) {
      this.onStatus?.({ label: "竞品聚合已降级", tone: "warning", reason: error.message });
    }
    this.clusterEngine = "markers";
    this._renderFallbackCompetitors(points);
  }

  _renderIndexCluster(context) {
    const data = (Array.isArray(context.clusterData) ? context.clusterData : [])
      .map((item) => ({ item, position: pointOf(item?.lnglat || item) }))
      .filter(({ position }) => position);
    if (!data.length || !context.marker) return;
    const key = context.index?.mainKey || "project";
    const count = Number(context.count || data.length);
    const name = key === "project" ? data[0].item.project : data[0].item[key] || "项目周边";
    const center = data.reduce((sum, entry) => [sum[0] + entry.position[0], sum[1] + entry.position[1]], [0, 0]).map((value) => value / data.length);
    const content = this._clusterContent({ count, name, single: count === 1, level: key });
    content.addEventListener("click", (event) => {
      event.stopPropagation();
      if (count === 1) this._selectDescriptor(data[0].item.descriptorId, data[0].position);
      else this.map.setZoomAndCenter?.(Math.min(18, Math.max(this.map.getZoom?.() || 12, Number(context.index?.maxZoom || 12)) + 1), center);
    });
    context.marker.setContent?.(content);
    context.marker.setPosition?.(center);
    context.marker.setAnchor?.("center");
  }

  _renderSpatialCluster(context) {
    const data = Array.isArray(context.clusterData) ? context.clusterData : [];
    const content = this._clusterContent({ count: context.count || data.length, name: "竞品样本", level: "spatial" });
    content.addEventListener("click", (event) => {
      event.stopPropagation();
      const position = context.marker?.getPosition?.();
      this.map.setZoomAndCenter?.(Math.min(18, (this.map.getZoom?.() || 13) + 2), position);
    });
    context.marker?.setContent?.(content);
    context.marker?.setAnchor?.("center");
  }

  _renderSpatialMarker(context) {
    const item = context.data?.[0] || context.data || context.clusterData?.[0];
    if (!item || !context.marker) return;
    const content = this._clusterContent({ count: 1, name: item.project, single: true, level: "project" });
    content.addEventListener("click", (event) => {
      event.stopPropagation();
      this._selectDescriptor(item.descriptorId, item.lnglat);
    });
    context.marker.setContent?.(content);
    context.marker.setAnchor?.("center");
  }

  _clusterContent({ count, name, single = false, level = "spatial" }) {
    const content = document.createElement("button");
    content.type = "button";
    content.className = `dds-cluster-marker${single ? " dds-cluster-marker--single" : ""}`;
    content.dataset.clusterLevel = level;
    content.setAttribute("aria-label", single ? String(name) : `${name}，${count} 个竞品样本`);
    const label = document.createElement("span");
    label.textContent = String(name || "竞品").slice(0, single ? 1 : 8);
    const total = document.createElement("strong");
    total.textContent = single ? "" : String(count);
    content.append(label, total);
    return content;
  }

  _renderFallbackCompetitors(points) {
    const AMap = globalThis.AMap;
    points.slice(0, 40).forEach((item) => {
      const content = this._clusterContent({ count: 1, name: item.project, single: true, level: "project" });
      const marker = new AMap.Marker({ position: item.lnglat, content, anchor: "center", zIndex: 126, zooms: [11, 20] });
      content.addEventListener("click", () => this._selectDescriptor(item.descriptorId, item.lnglat));
      this.map.add(marker);
      this.competitorMarkers.push(marker);
    });
  }

  _selectDescriptor(id, position) {
    const descriptor = this.descriptors.find((item) => String(item.id) === String(id));
    if (descriptor) this.onSelect?.({ descriptor, position: position || pointOf(descriptor) });
  }

  _setClusterVisible(visible) {
    this._clusterVisible = Boolean(visible);
    if (typeof this.competitorCluster?.setMap === "function") this.competitorCluster.setMap(this._clusterVisible ? this.map : null);
    this.competitorMarkers.forEach((marker) => this._clusterVisible ? marker.show?.() : marker.hide?.());
  }

  _clearCompetitors() {
    try {
      this.competitorCluster?.setMap?.(null);
      this.competitorCluster?.clearMarkers?.();
    } catch { /* Best-effort cleanup across both cluster plugins. */ }
    this.competitorCluster = null;
    if (this.competitorMarkers.length) this.map?.remove?.(this.competitorMarkers);
    this.competitorMarkers = [];
    this.clusterEngine = "none";
  }

  async searchNearby(category = "transit", { center = null, radius = 3000, city = "" } = {}) {
    const searchGeneration = ++this.poiSearchGeneration;
    const definition = POI_CATEGORIES[category] || POI_CATEGORIES.transit;
    const site = pointOf(center) || pointOf(this.descriptors.find((item) => item.id === "parcel-anchor"));
    if (!site) throw new Error("缺少可用于周边检索的地块坐标");
    this.poiCategory = category;
    this.onPoiUpdate?.({ category, label: definition.label, status: "loading", items: [] });
    await loadPlugins(["AMap.PlaceSearch"]);
    if (this.destroyed || searchGeneration !== this.poiSearchGeneration || !this.map) return [];
    if (!globalThis.AMap?.PlaceSearch) {
      const error = "高德 PlaceSearch 插件未加载";
      this.onPoiUpdate?.({ category, label: definition.label, status: "error", items: [], error });
      throw new Error(error);
    }
    const placeSearch = new globalThis.AMap.PlaceSearch({
      pageSize: 30,
      pageIndex: 1,
      city: city || undefined,
      citylimit: Boolean(city),
      type: definition.type,
      extensions: "all",
    });
    return new Promise((resolve) => {
      placeSearch.searchNearBy(definition.keyword, site, Math.max(500, Math.min(50000, Number(radius) || 3000)), (status, result) => {
        if (this.destroyed || searchGeneration !== this.poiSearchGeneration || !this.map) {
          resolve([]);
          return;
        }
        if (status !== "complete" || !Array.isArray(result?.poiList?.pois)) {
          const error = result?.info || "周边 POI 暂无可用结果";
          this._clearPoiMarkers();
          this.onPoiUpdate?.({ category, label: definition.label, status: "empty", items: [], error });
          resolve([]);
          return;
        }
        const items = result.poiList.pois.map((poi, index) => {
          const position = poiPosition(poi);
          if (!position) return null;
          return {
            id: `amap-poi-${category}-${poi.id || index}`,
            name: poi.name || `${definition.label} ${index + 1}`,
            category,
            categoryLabel: definition.label,
            position,
            distance: Number(poi.distance) || null,
            address: poi.address || "",
            type: poi.type || definition.type,
            tel: poi.tel || "",
            source: "AMap PlaceSearch",
          };
        }).filter(Boolean);
        this.poiResults = items;
        this._renderPoiMarkers(items, definition);
        this.onPoiUpdate?.({ category, label: definition.label, status: items.length ? "ready" : "empty", items });
        resolve(items);
      });
    });
  }

  _renderPoiMarkers(items, definition) {
    if (this.destroyed || !this.map) return;
    this._clearPoiMarkers();
    const AMap = globalThis.AMap;
    items.slice(0, 30).forEach((item) => {
      const content = document.createElement("button");
      content.type = "button";
      content.className = "dds-poi-marker";
      content.style.setProperty("--poi-color", definition.color);
      content.setAttribute("aria-label", item.name);
      const image = document.createElement("img");
      image.src = markerIcon(definition.color);
      image.alt = "";
      content.append(image);
      const marker = new AMap.Marker({ position: item.position, content, anchor: "center", zIndex: 132, zooms: [13, 20], title: item.name });
      content.addEventListener("click", (event) => {
        event.stopPropagation();
        this._openPoi(item);
      });
      this.map.add(marker);
      this.poiMarkers.set(item.id, marker);
    });
    this._setPoiVisible(this._poiVisible);
  }

  _openPoi(item) {
    const AMap = globalThis.AMap;
    const content = document.createElement("div");
    content.className = "dds-map-popover";
    const eyebrow = document.createElement("span");
    eyebrow.textContent = `${item.categoryLabel} · ${item.distance == null ? "距离待核" : `${Math.round(item.distance)} m`}`;
    const title = document.createElement("strong");
    title.textContent = item.name;
    const detail = document.createElement("small");
    detail.textContent = [item.address, item.type].filter(Boolean).join(" · ");
    content.append(eyebrow, title, detail);
    this.poiInfoWindow?.close?.();
    this.poiInfoWindow = new AMap.InfoWindow({ isCustom: true, content, offset: new AMap.Pixel(0, -16) });
    this.poiInfoWindow.open(this.map, item.position);
  }

  async routeToPoi(id, mode = "walking") {
    const item = this.poiResults.find((candidate) => String(candidate.id) === String(id));
    const origin = pointOf(this.descriptors.find((candidate) => candidate.id === "parcel-anchor"));
    if (!item || !origin) throw new Error("路线规划缺少起终点");
    const plugin = mode === "driving" ? "AMap.Driving" : "AMap.Walking";
    await loadPlugins([plugin]);
    const Service = mode === "driving" ? globalThis.AMap?.Driving : globalThis.AMap?.Walking;
    if (!Service || this.destroyed || !this.map) throw new Error("高德路线规划插件未加载");
    this.routeService?.clear?.();
    const service = new Service({ map: this.map, hideMarkers: false, showTraffic: mode === "driving", autoFitView: true });
    this.routeService = service;
    return new Promise((resolve, reject) => {
      service.search(origin, item.position, (status, result) => {
        if (this.destroyed || service !== this.routeService) return resolve(null);
        if (status !== "complete") return reject(new Error(result?.info || "高德路线规划失败"));
        const route = result?.routes?.[0] || {};
        resolve({ id: item.id, name: item.name, mode, distance: route.distance || item.distance, time: route.time || null, source: "AMap Route Planning" });
      });
    });
  }

  focusPoi(id) {
    const item = this.poiResults.find((candidate) => candidate.id === id);
    if (!item) return false;
    this.map.setZoomAndCenter?.(16, item.position);
    this._openPoi(item);
    return true;
  }

  _setPoiVisible(visible) {
    this._poiVisible = Boolean(visible);
    this.poiMarkers.forEach((marker) => this._poiVisible ? marker.show?.() : marker.hide?.());
  }

  _clearPoiMarkers() {
    const markers = [...this.poiMarkers.values()];
    if (markers.length) this.map?.remove?.(markers);
    this.poiMarkers.clear();
    this.poiInfoWindow?.close?.();
    this.routeService?.clear?.();
    this.routeService = null;
  }

  async startMeasure(type = "distance") {
    await loadPlugins(["AMap.MouseTool"]);
    if (!globalThis.AMap?.MouseTool) throw new Error("高德量测插件未加载");
    if (!this.mouseTool) this.mouseTool = new globalThis.AMap.MouseTool(this.map);
    this.mouseTool.close?.(false);
    if (type === "area") {
      this.mouseTool.measureArea({ strokeColor: "#5f9fbd", strokeWeight: 2, fillColor: "#5f9fbd", fillOpacity: 0.18 });
    } else {
      this.mouseTool.rule({ lineOptions: { strokeStyle: "solid", strokeColor: "#c87961", strokeOpacity: 0.95, strokeWeight: 3 } });
    }
    return type;
  }

  clearMeasure() {
    this.mouseTool?.close?.(true);
  }

  getSummary() {
    const competitors = this._clusterPoints();
    const buildingCounts = competitors.reduce((counts, item) => {
      const key = buildingClass(item.descriptor?.data);
      counts[key] = (counts[key] || 0) + 1;
      return counts;
    }, {});
    return {
      competitors: competitors.length,
      poi: this.poiResults.length,
      poiCategory: this.poiCategory,
      clusterEngine: this.clusterEngine,
      buildingCounts,
      buildingStyles: BUILDING_STYLES,
    };
  }

  describeDescriptor(descriptor) {
    const data = descriptor?.data || {};
    return {
      price: formatPrice(data.unit_price_cny ?? data.price),
      buildingClass: BUILDING_STYLES[buildingClass(data)].label,
    };
  }

  destroy() {
    this.destroyed = true;
    this.poiSearchGeneration += 1;
    this.clearMeasure();
    this._clearPoiMarkers();
    this._clearCompetitors();
    this.poiInfoWindow?.close?.();
    if (this.scaleControl) this.map?.removeControl?.(this.scaleControl);
    if (this.buildingZones.length) this.map?.remove?.(this.buildingZones);
    this.buildingZones = [];
    this.buildingLayer?.setStyle?.({ hideWithoutStyle: false, areas: [] });
    this.map = null;
  }
}

export { BUILDING_STYLES, POI_CATEGORIES };
