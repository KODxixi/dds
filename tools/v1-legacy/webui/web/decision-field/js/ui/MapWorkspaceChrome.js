const TOPICS = Object.freeze({
  overview: Object.freeze({ title: "空间总览", status: "DDS + 高德", mode: "3d", layers: { parcel: true, searchArea: true, competitors: true, resources: true, risks: false, buildings: true } }),
  site: Object.freeze({ title: "区位与边界", status: "高德空间事实", mode: "3d", layers: { parcel: true, searchArea: true, competitors: false, resources: false, risks: false, buildings: true } }),
  competitors: Object.freeze({ title: "竞品与价格", status: "DDS 市场样本", mode: "3d", layers: { parcel: true, searchArea: false, competitors: true, resources: false, risks: false, buildings: true } }),
  resources: Object.freeze({ title: "公共资源", status: "高德实时检索", mode: "2d", layers: { parcel: true, searchArea: true, competitors: false, resources: true, risks: false, buildings: false } }),
  buildings: Object.freeze({ title: "建筑与地块", status: "高德三维楼块", mode: "3d", layers: { parcel: true, searchArea: false, competitors: true, resources: false, risks: false, buildings: true } }),
  risks: Object.freeze({ title: "风险与证据缺口", status: "DDS 证据状态", mode: "2d", layers: { parcel: true, searchArea: true, competitors: false, resources: false, risks: true, buildings: false } }),
});

const POI_LABELS = Object.freeze({ transit: "交通", education: "教育", medical: "医疗", retail: "商业", park: "公园" });

function create(tag, className = "", text = null) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== null && text !== undefined) element.textContent = String(text);
  return element;
}

function pointOf(descriptor) {
  const point = descriptor?.amapPosition || descriptor?.position;
  const lng = Number(point?.lng ?? point?.[0]);
  const lat = Number(point?.lat ?? point?.[1]);
  return Number.isFinite(lng) && Number.isFinite(lat) ? [lng, lat] : null;
}

function formatNumber(value, fallback = "待核") {
  const number = Number(value);
  return Number.isFinite(number) ? new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 1 }).format(number) : fallback;
}

function descriptorMeta(descriptor) {
  const data = descriptor?.data || {};
  if (descriptor?.layer === "competitors") {
    const price = data.unit_price_cny ?? data.price;
    const distance = data.distance_km;
    return [price ? `${formatNumber(price)} 元/㎡` : "价格待核", distance != null ? `${formatNumber(distance)} km` : "距离待核"].join(" · ");
  }
  if (descriptor?.layer === "risks") return data.severity || data.grade || "等级待核";
  return descriptor?.source || "来源待核";
}

export class MapWorkspaceChrome {
  constructor(app) {
    this.app = app;
    this.root = app.root;
    this.scene = app.scene;
    this.store = app.store;
    this.evidenceLayers = app.evidenceLayers;
    this.topic = "overview";
    this.theme = "dds";
    this.poiCategory = "transit";
    this.poiState = { status: "idle", items: [] };
    this.siteContext = null;
    this.selectedId = null;
    this.userOpenedPanel = false;
    this.bound = [];
    this.unsubscribe = null;
  }

  mount() {
    this.panel = this.root.querySelector("#mapDataPanel");
    this.panelTitle = this.root.querySelector("#mapPanelTitle");
    this.panelStatus = this.root.querySelector("#mapPanelStatus");
    this.panelBody = this.root.querySelector("#mapPanelBody");
    this.poiControls = this.root.querySelector("#mapPoiControls");
    if (!this.panel || !this.panelBody) return this;
    this._listen(this.root, "click", (event) => this._handleClick(event));
    this._listen(this.root, "dds:map-poi", (event) => {
      this.poiState = event.detail || { status: "empty", items: [] };
      if (this.topic === "resources") this.render();
    });
    this._listen(this.root, "dds:map-feature", (event) => {
      this.selectedId = event.detail?.id || null;
      this.render();
    });
    this.unsubscribe = this.store.subscribe(() => this.render(), { immediate: true });
    this._applyTopic("overview", { preserveMode: true });
    if (globalThis.matchMedia?.("(max-width: 767px)")?.matches) {
      this.panel.setAttribute("aria-hidden", "true");
      this.root.classList.add("map-panel-collapsed");
    }
    return this;
  }

  _listen(target, type, listener, options) {
    target?.addEventListener(type, listener, options);
    this.bound.push([target, type, listener, options]);
  }

  async _handleClick(event) {
    const mode = event.target.closest?.("[data-map-mode]")?.dataset.mapMode;
    if (mode) {
      await this._setMode(mode);
      return;
    }
    const theme = event.target.closest?.("[data-map-theme]")?.dataset.mapTheme;
    if (theme) {
      this._setTheme(theme);
      return;
    }
    const topic = event.target.closest?.("[data-map-topic]")?.dataset.mapTopic;
    if (topic) {
      await this._applyTopic(topic);
      return;
    }
    const category = event.target.closest?.("[data-poi-category]")?.dataset.poiCategory;
    if (category) {
      await this._searchPoi(category);
      return;
    }
    const tool = event.target.closest?.("[data-map-tool]")?.dataset.mapTool;
    if (tool) {
      await this._setMeasure(tool);
      return;
    }
    const panelAction = event.target.closest?.("[data-map-panel]")?.dataset.mapPanel;
    if (panelAction) {
      const open = panelAction === "open";
      this.userOpenedPanel = open;
      this.panel.setAttribute("aria-hidden", String(!open));
      this.root.classList.toggle("map-panel-collapsed", !open);
      return;
    }
    const objectButton = event.target.closest?.("[data-map-object]");
    if (objectButton) this._focusDescriptor(objectButton.dataset.mapObject);
    const poiButton = event.target.closest?.("[data-map-poi]");
    if (poiButton) this.scene.focusPoi(poiButton.dataset.mapPoi);
    const routeButton = event.target.closest?.("[data-map-route]");
    if (routeButton) {
      const [id, modeName] = routeButton.dataset.mapRoute.split("|");
      await this.scene.routeToPoi(id, modeName).catch(() => null);
    }
  }

  async _setMode(mode) {
    await this.scene.setAmapMode(mode, { animate: true });
    this.root.querySelectorAll("[data-map-mode]").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.mapMode === mode)));
    this._setTheme(this.theme);
  }

  _setTheme(theme) {
    this.theme = theme;
    if (theme === "satellite") this.scene.setAmapBasemap("satellite");
    else {
      this.scene.setAmapBasemap("color");
      this.scene.setAmapTheme(theme);
    }
    this.root.querySelectorAll("[data-map-theme]").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.mapTheme === theme)));
  }

  async _applyTopic(id, { preserveMode = false } = {}) {
    const topic = TOPICS[id] || TOPICS.overview;
    this.topic = TOPICS[id] ? id : "overview";
    this.selectedId = null;
    Object.entries(topic.layers).forEach(([layer, visible]) => this.evidenceLayers.setVisible(layer, visible));
    this.scene.amapDecisionLayers?.setVisibility(topic.layers);
    this.scene.setAmapLiveVisibility(this.topic === "site");
    if (!preserveMode) await this._setMode(topic.mode);
    this.root.querySelectorAll("[data-map-topic]").forEach((button) => button.setAttribute("aria-current", String(button.dataset.mapTopic === this.topic)));
    this.poiControls.hidden = this.topic !== "resources";
    if (!preserveMode || !globalThis.matchMedia?.("(max-width: 767px)")?.matches) {
      this.panel.setAttribute("aria-hidden", "false");
      this.root.classList.remove("map-panel-collapsed");
      this.userOpenedPanel = true;
    }
    if (this.topic === "site") await this._loadSiteContext();
    if (this.topic === "resources" && this.poiState.status === "idle") await this._searchPoi(this.poiCategory);
    this.render();
  }

  async _loadSiteContext() {
    const state = this.store.snapshot();
    const location = state.location || state.project?.location || {};
    this.siteContext = { loading: true };
    this.render();
    try {
      this.siteContext = await this.scene.getAmapSiteContext({ center: location.gcj02 || location, city: location.city || "", district: location.district || "" });
    } catch (error) {
      this.siteContext = { error: error.message || "高德空间事实暂不可用" };
    }
  }

  async _searchPoi(category) {
    this.poiCategory = category;
    this.poiState = { status: "loading", label: POI_LABELS[category], items: [] };
    this.poiControls.querySelectorAll("[data-poi-category]").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.poiCategory === category)));
    this.render();
    const state = this.store.snapshot();
    const location = state.location || state.project?.location || {};
    await this.scene.searchNearby(category, { center: location.gcj02 || location, city: location.city || "", radius: 3000 }).catch((error) => {
      this.poiState = { status: "error", label: POI_LABELS[category], items: [], error: error.message };
      this.render();
    });
  }

  async _setMeasure(tool) {
    this.root.querySelectorAll("[data-map-tool]").forEach((button) => button.setAttribute("aria-pressed", "false"));
    if (tool === "clear") {
      this.scene.clearMeasure();
      return;
    }
    await this._setMode("2d");
    await this.scene.startMeasure(tool).catch(() => null);
    this.root.querySelector(`[data-map-tool="${tool}"]`)?.setAttribute("aria-pressed", "true");
  }

  _focusDescriptor(id) {
    const descriptor = this.evidenceLayers.descriptors.find((item) => String(item.id) === String(id));
    const point = pointOf(descriptor);
    if (!descriptor || !point) return;
    this.selectedId = String(id);
    this.scene.highlight([id]);
    this.scene.amapMap?.setZoomAndCenter?.(17, point);
    this.render();
  }

  _appendMetrics(container, metrics) {
    const list = create("dl", "map-panel-metrics");
    metrics.forEach(([label, value]) => {
      const item = create("div");
      item.append(create("dt", "", label), create("dd", "", value));
      list.append(item);
    });
    container.append(list);
  }

  _appendDescriptors(container, descriptors, heading) {
    const title = create("div", "map-panel-section-title");
    title.append(create("strong", "", heading), create("span", "", `${descriptors.length} 条`));
    container.append(title);
    const list = create("div", "map-panel-list");
    descriptors.slice(0, 16).forEach((descriptor) => {
      const button = create("button", "map-panel-row");
      button.type = "button";
      button.dataset.mapObject = descriptor.id;
      if (String(descriptor.id) === this.selectedId) button.setAttribute("aria-current", "true");
      button.append(create("strong", "", descriptor.label), create("span", "", descriptorMeta(descriptor)), create("small", "", descriptor.source || "来源待核"));
      list.append(button);
    });
    if (!descriptors.length) list.append(create("p", "map-panel-empty", "当前专题暂无已确认的空间对象。"));
    container.append(list);
  }

  _renderSite(container, location) {
    if (this.siteContext?.loading) {
      container.append(create("p", "map-panel-empty", "正在读取高德地址、行政区、天气和实时路况…"));
      return;
    }
    if (this.siteContext?.error) {
      container.append(create("p", "map-panel-empty", this.siteContext.error));
      return;
    }
    const context = this.siteContext || {};
    const weather = context.weather || {};
    const detail = create("div", "map-panel-address");
    detail.append(create("strong", "", context.formattedAddress || location.display_name || "地址待核"), create("span", "", [context.district, context.township, context.adcode].filter(Boolean).join(" · ") || "行政区待核"));
    container.append(detail);
    this._appendMetrics(container, [
      ["天气", weather.condition || "待核"],
      ["温度", weather.temperature == null ? "待核" : `${weather.temperature}°C`],
      ["行政边界", `${Number(context.boundaryCount || 0)} 组`],
      ["实时路况", context.traffic ? "已接入" : "不可用"],
    ]);
  }

  _renderPoi(container) {
    if (this.poiState.status === "loading") {
      container.append(create("p", "map-panel-empty", `正在检索 3 km 内${this.poiState.label || "公共"}设施…`));
      return;
    }
    const items = this.poiState.items || [];
    const title = create("div", "map-panel-section-title");
    title.append(create("strong", "", `${this.poiState.label || "周边"}设施`), create("span", "", `${items.length} 条`));
    container.append(title);
    const list = create("div", "map-panel-list");
    items.slice(0, 18).forEach((item) => {
      const row = create("div", "map-panel-poi");
      const focus = create("button", "map-panel-row");
      focus.type = "button";
      focus.dataset.mapPoi = item.id;
      focus.append(create("strong", "", item.name), create("span", "", item.distance == null ? "距离待核" : `${Math.round(item.distance)} m`), create("small", "", item.address || item.type || "高德 POI"));
      const actions = create("div", "map-panel-poi__actions");
      const walk = create("button", "", "步行"); walk.type = "button"; walk.dataset.mapRoute = `${item.id}|walking`;
      const drive = create("button", "", "驾车"); drive.type = "button"; drive.dataset.mapRoute = `${item.id}|driving`;
      actions.append(walk, drive);
      row.append(focus, actions);
      list.append(row);
    });
    if (!items.length) list.append(create("p", "map-panel-empty", this.poiState.error || "当前类别暂无可定位设施。"));
    container.append(list);
  }

  render() {
    if (!this.panelBody) return;
    const topic = TOPICS[this.topic] || TOPICS.overview;
    const state = this.store.snapshot();
    if ((state.activeQuestion || (state.location && !state.project)) && globalThis.matchMedia?.("(max-width: 767px)")?.matches) {
      this.panel.setAttribute("aria-hidden", "true");
      this.root.classList.add("map-panel-collapsed");
      this.userOpenedPanel = false;
    }
    const location = state.location || state.project?.location || {};
    const descriptors = this.evidenceLayers.descriptors || [];
    const groups = {
      competitors: descriptors.filter((item) => item.layer === "competitors"),
      resources: descriptors.filter((item) => item.layer === "resources"),
      risks: descriptors.filter((item) => item.layer === "risks"),
      parcel: descriptors.filter((item) => ["parcel", "searchArea"].includes(item.layer)),
    };
    this.panelTitle.textContent = topic.title;
    this.panelStatus.textContent = topic.status;
    this.panelBody.replaceChildren();
    if (this.topic === "site") this._renderSite(this.panelBody, location);
    else if (this.topic === "resources") this._renderPoi(this.panelBody);
    else {
      this._appendMetrics(this.panelBody, [
        ["竞品样本", String(groups.competitors.length)],
        ["已入库资源", String(groups.resources.length)],
        ["空间风险", String(groups.risks.length)],
        ["坐标口径", location.gcj02 ? "GCJ-02" : "待核"],
      ]);
      if (this.topic === "competitors" || this.topic === "buildings") this._appendDescriptors(this.panelBody, groups.competitors, this.topic === "buildings" ? "具备真实范围的建筑样本" : "相关竞品");
      else if (this.topic === "risks") this._appendDescriptors(this.panelBody, groups.risks, "空间风险与缺口");
      else this._appendDescriptors(this.panelBody, [...groups.parcel, ...groups.competitors.slice(0, 8)], "当前空间对象");
    }
    const note = create("p", "map-panel-provenance", "地图事实来自高德 JS API；业务样本、判断与证据等级来自 DDS。无真实边界的数据不会着色为建筑类型。");
    this.panelBody.append(note);
  }

  destroy() {
    this.unsubscribe?.();
    this.bound.forEach(([target, type, listener, options]) => target?.removeEventListener(type, listener, options));
    this.bound = [];
  }
}
