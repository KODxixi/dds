const SECTIONS = Object.freeze([
  { id: "decision", title: "Decision", subtitle: "进入结论、边界与置信度", preset: "city", height: 650000, layers: { parcel: true, competitors: true } },
  { id: "site", title: "Site", subtitle: "位置、交通、资源与法定约束", preset: "site", layers: { parcel: true, searchArea: true, resources: true } },
  { id: "market", title: "Market", subtitle: "竞品、价格带、去化与缺口", preset: "evidence", layers: { parcel: true, competitors: true, heat: true } },
  { id: "customer", title: "Customer", subtitle: "客群、支付意愿与购买阻力", preset: "context", layers: { parcel: true, resources: true } },
  { id: "product", title: "Product", subtitle: "面积段、户配、总价与错配风险", preset: "site", layers: { parcel: true } },
  { id: "design", title: "Design", subtitle: "总图、户型、立面、会所与景观任务", preset: "presentation", layers: { parcel: true, buildings: true } },
  { id: "finance", title: "Finance", subtitle: "地价边界、地货比、IRR 与敏感性", preset: "context", layers: { parcel: true, competitors: true, heat: true, risks: true } },
  { id: "risk", title: "Risk", subtitle: "Hard stop、冲突、待补证据与人审项", preset: "context", layers: { parcel: true, risks: true } },
  { id: "provenance", title: "Provenance", subtitle: "来源等级、时间、样本与方法", preset: "evidence", layers: { parcel: true } },
]);

const LABELS = Object.freeze({
  summary: "结论摘要",
  verdict: "进入判断",
  recommendation: "建议",
  confidence: "置信度",
  overall_confidence: "综合置信度",
  score: "评分",
  city: "城市",
  address: "位置",
  district: "行政区",
  lng: "经度",
  lat: "纬度",
  far: "容积率",
  expected_price: "目标售价",
  sample_size: "样本数",
  price: "价格",
  price_band: "价格带",
  irr: "IRR",
  land_price: "地价边界",
  generated_at: "生成时间",
  updated_at: "更新时间",
  source: "来源",
  method: "方法",
  revision: "Revision",
  state: "项目状态",
});

function createElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = String(text);
  return element;
}

function isScalar(value) {
  return value == null || ["string", "number", "boolean"].includes(typeof value);
}

function humanLabel(key) {
  if (LABELS[key]) return LABELS[key];
  return String(key || "").replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatValue(value, key = "") {
  if (value == null || value === "") return "待补";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "number") {
    if (/confidence|ratio|rate/i.test(key) && value >= 0 && value <= 1) return `${Math.round(value * 100)}%`;
    if (/irr/i.test(key) && Math.abs(value) <= 1) return `${(value * 100).toFixed(1)}%`;
    return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(value);
  }
  return String(value);
}

function firstObject(...values) {
  return values.find((value) => value && typeof value === "object" && !Array.isArray(value)) || null;
}

function plainText(value) {
  return String(value || "")
    .replace(/\*\*(.*?)\*\*/g, "$1")
    .replace(/__(.*?)__/g, "$1")
    .replace(/^#{1,6}\s+/gm, "")
    .trim();
}

function firstString(value) {
  if (typeof value === "string" && value.trim()) return plainText(value);
  if (!value || typeof value !== "object") return "";
  for (const key of ["summary", "verdict", "recommendation", "conclusion", "description", "message", "strategy"]) {
    if (typeof value[key] === "string" && value[key].trim()) return plainText(value[key]);
  }
  return "";
}

function headlineSummary(value, limit = 110) {
  const clean = plainText(value)
    .replace(/^决策建议\s*/i, "")
    .replace(/^\d+[.、]\s*/, "")
    .replace(/\s+/g, " ")
    .trim();
  const sentence = clean.split(/(?<=[。！？!?])\s*/)[0] || clean;
  return sentence.length > limit ? `${sentence.slice(0, limit).trim()}…` : sentence;
}

function isSensitiveKey(key) {
  return /(?:(?:api|amap)[_-]?(?:js[_-]?)?key|secret|security[_-]?code|token|password|credential)/i.test(String(key || ""));
}

function collectMetrics(value, limit = 8) {
  if (!value || typeof value !== "object") return [];
  const metrics = [];
  Object.entries(value).forEach(([key, item]) => {
    if (metrics.length >= limit || isSensitiveKey(key) || !isScalar(item) || item == null || item === "") return;
    if (["summary", "description", "message", "recommendation", "conclusion"].includes(key)) return;
    metrics.push({ key, label: humanLabel(key), value: formatValue(item, key) });
  });
  return metrics;
}

function itemText(item) {
  if (isScalar(item)) return formatValue(item);
  if (!item || typeof item !== "object") return "";
  if (item.error) return String(item.label || item.name || "\u6570\u636e\u6e90") + "\uff1a\u6682\u65f6\u4e0d\u53ef\u7528";
  const lead = firstString(item);
  if (lead) return lead;
  const values = Object.entries(item)
    .filter(([, value]) => isScalar(value) && value != null && value !== "")
    .slice(0, 4)
    .map(([key, value]) => `${humanLabel(key)}：${formatValue(value, key)}`);
  return values.join(" · ");
}

function collectFindings(value, limit = 12) {
  if (!value) return [];
  if (Array.isArray(value)) return value.map(itemText).filter(Boolean).slice(0, limit);
  if (typeof value !== "object") return firstString(value) ? [firstString(value)] : [];
  const priorityKeys = ["findings", "recommendations", "items", "risks", "hard_stops", "evidence_gaps", "strategies", "actions", "constraints", "opportunities", "resources", "competitors"];
  const findings = [];
  priorityKeys.forEach((key) => {
    const source = value[key];
    if (Array.isArray(source)) findings.push(...source.map(itemText).filter(Boolean));
    else if (source && typeof source === "object") findings.push(...Object.values(source).map(itemText).filter(Boolean));
  });
  if (!findings.length) {
    Object.entries(value).forEach(([key, item]) => {
      if (isScalar(item) || item == null) return;
      const text = itemText(item);
      if (text) findings.push(`${humanLabel(key)}：${text}`);
    });
  }
  return [...new Set(findings)].slice(0, limit);
}

function projectLocation(project, report) {
  return project?.location || report?.location || {
    display_name: report?.parcel?.address,
    address: report?.parcel?.address,
    city: report?.parcel?.city,
    wgs84: report?.parcel?.wgs84,
  };
}

function reportDecision(report, project) {
  const revisions = project?.decision_revisions || [];
  return report?.decision || revisions[revisions.length - 1] || {};
}

function safeUrl(value) {
  if (typeof value !== "string" || !value.trim()) return null;
  try {
    const url = new URL(value, globalThis.location.origin);
    if (!["http:", "https:"].includes(url.protocol) || url.origin !== globalThis.location.origin) return null;
    return `${url.pathname}${url.search}${url.hash}`;
  } catch {
    return null;
  }
}

export class ReportApp {
  constructor({ root, config, api, store, scene, director, evidenceLayers, sceneStore, layerRegistry, mapShell, analysisPanel }) {
    if (!root) throw new TypeError("Report root is required");
    this.root = root;
    this.config = config;
    this.api = api;
    this.store = store;
    this.scene = scene;
    this.director = director;
    this.evidenceLayers = evidenceLayers;
    this.sceneStore = sceneStore;
    this.layerRegistry = layerRegistry;
    this.mapShell = mapShell;
    this.analysisPanel = analysisPanel;
    this.project = null;
    this.report = null;
    this.projectId = config.projectId;
    this.elements = {};
    this.bound = [];
    this.sectionObserver = null;
    this.activeSection = "decision";
    this.presentationIndex = 0;
    this.presentation = false;
    this.baseScene = false;
    this.destroyed = false;
    this.narrativeTimer = null;
    this.unsubscribeScene = null;
    this.activePoiCategory = "transit";
    this.activeMapTool = null;
    this.documentOnly = String(root.dataset.surface || "").toLowerCase() === "document";
  }

  async mount() {
    this._cacheElements();
    this._bindEvents();
    if (!this.projectId) {
      this._showFatal("无法从报告 URL 读取 project_id");
      return this;
    }

    if (this.documentOnly) {
      try {
        const payload = await this.api.getReportData(this.projectId);
        this.project = payload.project || payload.data?.project;
        this.report = payload.report || payload.data?.report;
        if (!this.project || !this.report) throw new Error("报告接口缺少 project 或 report");
        this.store.ingest(payload, "report-data");
        this._renderReport();
        this.root.dataset.state = "ready";
        this.elements.reportContent.setAttribute("aria-busy", "false");
        this._observeSections();
        this._announce("决策报告已加载");
      } catch (error) {
        this._showFatal(error.message || String(error));
      }
      return this;
    }

    let bootstrap = {};
    let amapConfig = null;
    try {
      bootstrap = await this.api.bootstrap();
      try {
        const mapConfig = await fetch("/api/map_config", { credentials: "same-origin" }).then((response) => response.ok ? response.json() : null);
        if (mapConfig?.amap_js_key) amapConfig = { key: mapConfig.amap_js_key, securityCode: mapConfig.amap_security_code || "" };
      } catch { /* AMap is optional; Cesium remains the fallback. */ }
    } catch (error) {
      this._announce(`场景配置读取失败：${error.message}`);
    }
    const sceneState = await this.scene.initialize({
      ...this.config.scene,
      ...(bootstrap.scene || {}),
      force2D: true,
      ionToken: bootstrap.scene?.cesium_ion_token || bootstrap.scene?.ionToken || this.config.scene.ionToken,
      cesiumBase: this.config.cesiumBase,
      worldTextureUrl: this.config.asset("assets/earth_real_50m.png"),
      quality: this.config.quality,
      reducedMotion: this.config.reducedMotion,
      amap: amapConfig || this.config.amap,
      container: this.elements.reportCesiumContainer,
      fallbackCanvas: this.elements.reportFallbackCanvas,
      onStatus: (status) => {
        this.elements.reportSceneStatus.textContent = status.label || status.mode || "场景";
        if (status.reason && status.tone !== "ok") this._announce(status.reason);
      },
      onUserInteraction: (input) => this.director.cancel(input || "user_interaction"),
      onFeatureSelected: (id) => this.sceneStore.select(id),
      onPoiUpdate: (payload) => {
        this.analysisPanel.setPoiResults(payload);
        const state = this.sceneStore.snapshot();
        this.analysisPanel.render(state.manifest, state.chapter, state.selectedId, { topic: state.topic });
      },
    });
    this.baseScene = Boolean(sceneState?.baseScene);
    this.director.bindUserControls(this.elements.reportCesiumContainer.parentElement);

    try {
      const payload = await this.api.getReportData(this.projectId);
      this.project = payload.project || payload.data?.project;
      this.report = payload.report || payload.data?.report;
      if (!this.project || !this.report) throw new Error("报告接口缺少 project 或 report");
      this.store.ingest(payload, "report-data");
      const descriptors = this.evidenceLayers.sync(payload, { location: this.project.location });
      const manifest = payload.scene_manifest || this._legacyManifest(payload);
      this.sceneStore.ingest(this._enrichManifest(manifest, descriptors));
      this._renderReport();
      await this.mapShell.initialize(this.project.location);
      this._renderTopics();
      this.unsubscribeScene = this.sceneStore.subscribe((state, reason) => this._syncSceneState(state, reason));
      this.director.registerBookmarks(this.project.scene_bookmarks || this.report.scene_bookmarks || []);
      await this._focusSection("decision", { moveDocument: false });
      this.root.dataset.state = "ready";
      this.elements.reportContent.setAttribute("aria-busy", "false");
      this._observeSections();
      this._announce("实时报告已加载");
    } catch (error) {
      this._showFatal(error.message || String(error));
    }
    return this;
  }

  _cacheElements() {
    const byId = (id) => this.root.querySelector(`#${id}`);
    const required = [
      "reportProjectName", "reportSceneLabel", "reportDecisionSummary", "reportNav", "reportContent",
      "presentationOverlay", "presentationIndex", "presentationTitle", "presentationText", "reportLiveRegion",
    ];
    const spatial = [
      "reportCesiumContainer", "reportFallbackCanvas", "reportSceneStatus", "reportCommandDock",
      "reportCommandInput", "mapTopicBar", "mapPoiBar", "reportAnalysisPanel", "sceneTimeline", "sceneTimelineLabel",
    ];
    [...required, ...(this.documentOnly ? [] : spatial)].forEach((id) => { this.elements[id] = byId(id); });
    this.elements.reportMapLink = byId("reportMapLink");
    const missing = Object.entries(this.elements).filter(([, element]) => !element).map(([id]) => id);
    if (missing.length) throw new Error(`Report DOM is incomplete: ${missing.join(", ")}`);
  }

  _listen(target, type, listener, options) {
    target?.addEventListener(type, listener, options);
    this.bound.push([target, type, listener, options]);
  }

  _bindEvents() {
    this._listen(this.root, "click", (event) => this._handleClick(event));
    this._listen(this.elements.reportCommandDock, "submit", (event) => {
      event.preventDefault();
      this._runCommand();
    });
    this._listen(document, "keydown", (event) => this._handleKeydown(event));
    this._listen(this.elements.sceneTimeline, "input", () => {
      const index = Math.max(0, Math.min(SECTIONS.length - 1, Number(this.elements.sceneTimeline.value)));
      this._focusSection(SECTIONS[index].id, { moveDocument: false });
    });
    this._listen(globalThis, "beforeunload", () => this.destroy(), { once: true });
  }

  _handleClick(event) {
    const sectionButton = event.target.closest?.("button[data-section-target]");
    if (sectionButton) {
      this._focusSection(sectionButton.dataset.sectionTarget, { moveDocument: true });
      return;
    }
    const modeButton = event.target.closest?.("button[data-map-mode]");
    if (modeButton) { this.sceneStore.patch({ mode: modeButton.dataset.mapMode }, "mode-control"); return; }
    const topicButton = event.target.closest?.("button[data-map-topic]");
    if (topicButton) {
      const topic = this.layerRegistry.topic(topicButton.dataset.mapTopic);
      this.sceneStore.patch({ topic: topic.id, mode: topic.mode || "2d" }, "topic-control");
      if (topic.id === "access") this._searchPoi(this.activePoiCategory);
      return;
    }
    const basemapButton = event.target.closest?.("button[data-basemap]");
    if (basemapButton) { this.sceneStore.patch({ basemap: basemapButton.dataset.basemap, mode: "2d" }, "basemap-control"); return; }
    const poiButton = event.target.closest?.("button[data-poi-category]");
    if (poiButton) { this._searchPoi(poiButton.dataset.poiCategory); return; }
    const toolButton = event.target.closest?.("button[data-map-tool]");
    if (toolButton) { this._activateMapTool(toolButton.dataset.mapTool); return; }
    const action = event.target.closest?.("[data-action]")?.dataset.action;
    const actions = {
      present: () => this.enterPresentation(),
      "export-html": () => this.exportReport("html"),
      "export-json": () => this.exportReport("json"),
      "previous-slide": () => this.showPresentationSlide(this.presentationIndex - 1),
      "next-slide": () => this.showPresentationSlide(this.presentationIndex + 1),
      "reset-map": () => this.mapShell.reset(),
      "toggle-narrative": () => this._toggleNarrative(),
      "toggle-timeline": () => this.root.classList.toggle("timeline-open"),
      "open-analysis": () => this.root.classList.remove("analysis-panel-collapsed"),
    };
    actions[action]?.();
  }

  _renderTopics() {
    this.elements.mapTopicBar.replaceChildren();
    const topics = ["overview", "site", "access", "live", "price", "supply", "building", "risk", "evidence"];
    topics.forEach((id) => {
      const topic = this.layerRegistry.topic(id);
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.mapTopic = id;
      button.textContent = topic.label;
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        this.sceneStore.patch({ topic: id, mode: topic.mode || "2d" }, "topic-control");
        if (id === "access") this._searchPoi(this.activePoiCategory);
        if (id === "live") this._loadAmapLive();
      });
      this.elements.mapTopicBar.append(button);
    });
  }

  async _syncSceneState(state, reason) {
    if (!state.manifest) return;
    if (this.mapShell.mode !== state.mode) await this.mapShell.setMode(state.mode);
    state = this.sceneStore.snapshot();
    if (this.mapShell.basemap !== state.basemap) this.mapShell.setBasemap(state.basemap);
    this.root.dataset.mapMode = state.mode;
    this.root.dataset.mapTopic = state.topic;
    this.mapShell.setLiveVisibility(state.topic === "live");
    this.root.querySelectorAll("button[data-map-mode]").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.mapMode === state.mode)));
    this.root.querySelectorAll("button[data-basemap]").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.basemap === state.basemap)));
    this.elements.mapTopicBar.querySelectorAll("button[data-map-topic]").forEach((button) => button.setAttribute("aria-current", String(button.dataset.mapTopic === state.topic)));
    const chapter = this.sceneStore.chapter(state.chapter);
    const layers = state.topic !== "overview" ? this.layerRegistry.topic(state.topic).layers : chapter?.layers;
    if (layers) this.layerRegistry.apply(layers);
    if (state.selectedId) this.scene.highlight([state.selectedId]);
    else this.scene.highlight([]);
    this.analysisPanel.render(state.manifest, state.chapter, state.selectedId, { topic: state.topic });
  }

  async _loadAmapLive() {
    const state = this.sceneStore.snapshot();
    this.analysisPanel.setAmapContext(null);
    this.analysisPanel.render(state.manifest, state.chapter, state.selectedId, { topic: "live" });
    try {
      const context = await this.mapShell.getSiteContext();
      this.analysisPanel.setAmapContext(context);
    } catch (error) {
      this.analysisPanel.setAmapContext({ source: "AMap JS API 2.0", formattedAddress: error.message || "高德实况暂不可用", traffic: false, boundaryCount: 0 });
    }
    const latest = this.sceneStore.snapshot();
    if (latest.topic === "live") this.analysisPanel.render(latest.manifest, latest.chapter, latest.selectedId, { topic: "live" });
  }

  _legacyManifest(payload) {
    const descriptors = this.evidenceLayers.build(payload, { location: this.project?.location });
    const layers = ["parcel", "competitors", "resources", "risks"].map((id) => ({ id, features: descriptors.filter((item) => item.layer === id).map((item) => ({ id: item.id, label: item.label, category: id, source: item.source, scenarioStatus: id === "risks" ? "inferred" : "observed", metrics: item.data || {} })) }));
    return { schema: "dds-map-scene/v2", projectId: this.projectId, coordinateSystem: "gcj02", site: {}, layers, metrics: { market: this.report?.market || {}, risk: { items: layers.find((item) => item.id === "risks")?.features || [] } }, timelines: {}, scenarios: [], evidence: {}, chapters: SECTIONS.map((section) => ({ id: section.id, mode: ["decision", "site", "design"].includes(section.id) ? "3d" : "2d", preset: section.preset, layers: Object.entries(section.layers).filter(([, visible]) => visible).map(([id]) => id) })) };
  }

  _enrichManifest(manifest, descriptors = []) {
    const allowed = [
      "distance_m", "distance_km", "unit_price_cny", "unit_price", "price",
      "city", "district", "sub_district", "address", "property_type", "building_type",
      "sale_status", "status", "floor_area_ratio", "plot_ratio", "total_units", "opening_date",
    ];
    const descriptorById = new Map(descriptors.map((item) => [String(item.id), item]));
    const layers = (manifest.layers || []).map((layer) => ({
      ...layer,
      features: (layer.features || []).map((feature) => {
        const descriptor = descriptorById.get(String(feature.id));
        if (!descriptor?.data) return feature;
        const details = Object.fromEntries(allowed.filter((key) => descriptor.data[key] != null).map((key) => [key, descriptor.data[key]]));
        return { ...feature, metrics: { ...details, ...(feature.metrics || {}) } };
      }),
    }));
    return { ...manifest, layers };
  }

  async _searchPoi(category = "transit") {
    this.activePoiCategory = category;
    this.elements.mapPoiBar.querySelectorAll("[data-poi-category]").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.poiCategory === category));
    });
    const current = this.sceneStore.snapshot();
    if (current.topic !== "access" || current.mode !== "2d") {
      this.sceneStore.patch({ topic: "access", mode: "2d" }, "poi-control");
    }
    try {
      await this.mapShell.setMode("2d", { notify: false });
      await this.mapShell.searchNearby(category, { radius: 3000 });
    } catch (error) {
      this._announce(error.message || "周边设施检索失败");
    }
  }

  async _activateMapTool(tool) {
    this.root.querySelectorAll("[data-map-tool]").forEach((button) => button.setAttribute("aria-pressed", "false"));
    if (tool === "clear") {
      this.mapShell.clearMeasure();
      this.activeMapTool = null;
      this._announce("已清除量测结果");
      return;
    }
    this.activeMapTool = tool;
    this.sceneStore.patch({ mode: "2d" }, "map-tool");
    try {
      await this.mapShell.setMode("2d", { notify: false });
      await this.mapShell.startMeasure(tool === "area" ? "area" : "distance");
      this.root.querySelector(`[data-map-tool="${tool}"]`)?.setAttribute("aria-pressed", "true");
      this._announce(tool === "area" ? "请在地图上绘制量测范围" : "请在地图上依次点击测距点");
    } catch (error) {
      this.activeMapTool = null;
      this._announce(error.message || "量测工具暂不可用");
    }
  }

  _toggleNarrative() {
    this.root.classList.add("timeline-open");
    if (this.narrativeTimer) { clearInterval(this.narrativeTimer); this.narrativeTimer = null; this.sceneStore.patch({ playing: false }, "narrative-pause"); this.root.classList.remove("narrative-playing"); return; }
    this.sceneStore.patch({ playing: true }, "narrative-play");
    this.root.classList.add("narrative-playing");
    this.narrativeTimer = setInterval(() => { const next = (Number(this.elements.sceneTimeline.value) + 1) % SECTIONS.length; this.elements.sceneTimeline.value = String(next); this._focusSection(SECTIONS[next].id, { moveDocument: false }); }, 2600);
  }

  _handleKeydown(event) {
    if (event.key === "Escape") {
      this.director.cancel("escape");
      if (this.presentation) this.exitPresentation();
      return;
    }
    if (!this.presentation) return;
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      this.showPresentationSlide(this.presentationIndex - 1);
    }
    if (["ArrowRight", " ", "PageDown"].includes(event.key)) {
      event.preventDefault();
      this.showPresentationSlide(this.presentationIndex + 1);
    }
  }

  _renderReport() {
    const location = projectLocation(this.project, this.report);
    const decision = reportDecision(this.report, this.project);
    const summary = headlineSummary(firstString(decision) || this.project.report?.summary || "联席判断已形成");
    const decisionFull = this.report.decision_full || {};
    const blueprint = decisionFull.blueprint_logic || {};
    this.elements.reportProjectName.textContent = location?.display_name || location?.address || `项目 ${this.project.id.slice(0, 8)}`;
    this.elements.reportDecisionSummary.textContent = summary;
    this.elements.reportSceneLabel.textContent = `${location?.city || "PROJECT"} / REV ${this.project.report?.revision || this.project.version || 1}`;
    if (this.elements.reportMapLink) this.elements.reportMapLink.href = this.config.mapUrl(this.project.id);
    document.title = `${this.elements.reportProjectName.textContent} · DDS 决策报告`;

    const sectionData = {
      decision,
      site: {
        ...(this.report.parcel || {}),
        ...(this.report.site || {}),
        resources: this.report.site?.resources || this.report.site?.pois || this.report.amenities,
        constraints: this.report.constraints,
      },
      market: firstObject(this.report.market, this.report.competition),
      customer: firstObject(this.report.customer, this.report.customer_strategy, this.report.audience, decisionFull.abm_market_agent, decisionFull.migration_agent),
      product: firstObject(this.report.product, this.report.product_strategy, this.report.unit_mix, decisionFull.unit_mix_agent),
      design: firstObject(this.report.design, this.report.design_brief, this.report.architecture, blueprint),
      finance: firstObject(this.report.finance, this.report.financial, this.report.economics, blueprint.financial_indicator, blueprint),
      risk: {
        ...(this.report.risk || {}),
        risks: blueprint.risk_curve || this.report.risks,
        hard_stops: this.report.hard_stops,
        evidence_gaps: this.project.evidence_gaps,
      },
      provenance: {
        ...(this.report.meta || {}),
        ...(this.report.provenance || {}),
        sources: this.report.provenance?.sources || this.report.sources,
        generated_at: this.report.meta?.generated_at,
        revision: this.project.report?.revision,
        state: this.project.state,
      },
    };
    SECTIONS.forEach((section) => this._renderSection(section.id, sectionData[section.id]));
  }

  _renderSection(sectionId, data) {
    const container = this.root.querySelector(`[data-section-content="${sectionId}"]`);
    if (!container) return;
    container.replaceChildren();
    const lead = firstString(data);
    if (lead) container.append(createElement("p", "report-lead", lead));
    const metrics = collectMetrics(data);
    if (metrics.length) {
      const list = createElement("dl", "report-metrics");
      metrics.forEach((metric) => {
        const item = createElement("div");
        item.append(createElement("dt", "", metric.label), createElement("dd", "", metric.value));
        list.append(item);
      });
      container.append(list);
    }
    if (sectionId === "provenance") {
      const sources = this._extractSources(data);
      if (sources.length) {
        const list = createElement("ul", "source-list");
        sources.forEach((source) => {
          const item = createElement("li");
          item.append(
            createElement("strong", "", source.name),
            createElement("span", "", source.detail),
            createElement("small", "", source.meta),
          );
          list.append(item);
        });
        container.append(list);
      }
    } else {
      const findings = collectFindings(data);
      if (findings.length) {
        const list = createElement("ul", "finding-list");
        findings.forEach((finding) => list.append(createElement("li", "", finding)));
        container.append(list);
      }
    }
    if (!container.childElementCount) {
      container.append(createElement("p", "report-empty", "当前 revision 暂无结构化内容，已列为待补证据。"));
    }
  }

  _extractSources(data) {
    const source = data?.sources || data?.items || data;
    if (Array.isArray(source)) {
      return source.slice(0, 20).map((item, index) => ({
        name: item?.name || item?.title || item?.source || `来源 ${index + 1}`,
        detail: itemText(item) || "已记录",
        meta: [item?.grade, item?.date, item?.sample].filter(Boolean).join(" · "),
      }));
    }
    if (!source || typeof source !== "object") return [];
    return Object.entries(source).filter(([key]) => !isSensitiveKey(key)).slice(0, 20).map(([key, value]) => ({
      name: humanLabel(key),
      detail: itemText(value) || formatValue(value),
      meta: typeof value === "object" ? [value?.grade, value?.date, value?.sample].filter(Boolean).join(" · ") : "",
    }));
  }

  async _focusSection(sectionId, { moveDocument = false } = {}) {
    const section = SECTIONS.find((item) => item.id === sectionId);
    if (!section || !this.project) return;
    this.activeSection = section.id;
    if (this.documentOnly) {
      this._setActiveNav(section.id);
      if (moveDocument) {
        this.root.querySelector(`#section-${section.id}`)?.scrollIntoView({
          behavior: this.config.reducedMotion ? "auto" : "smooth",
          block: "start",
        });
      }
      return;
    }
    const manifestChapter = this.sceneStore.chapter(section.id);
    this.sceneStore.patch({
      chapter: section.id,
      topic: "overview",
      selectedId: null,
      ...(this.presentation && manifestChapter?.mode ? { mode: manifestChapter.mode } : {}),
    }, "chapter");
    const sectionIndex = SECTIONS.findIndex((item) => item.id === section.id);
    this.elements.sceneTimeline.value = String(Math.max(0, sectionIndex));
    this.elements.sceneTimelineLabel.textContent = section.title;
    this._setActiveNav(section.id);
    if (moveDocument) {
      this.root.querySelector(`#section-${section.id}`)?.scrollIntoView({
        behavior: this.config.reducedMotion ? "auto" : "smooth",
        block: "start",
      });
    }
    this.evidenceLayers.clearHighlight();

    const bookmark = this._bookmarkFor(section.id);
    if (bookmark) {
      await this.director.execute({ type: "restore_bookmark", bookmark });
    } else if (this.project.location?.wgs84) {
      const baseOverview = this.scene.activeEngine !== "amap" && this.baseScene && section.id === "decision";
      const location = this.scene.activeEngine === "amap"
        ? (this.project.location.gcj02 || this.project.location.wgs84)
        : this.project.location.wgs84;
      await this.director.execute({
        type: "fly_to",
        target: { ...location, height: baseOverview ? 12000000 : section.height },
        preset: baseOverview ? "globe" : section.preset,
      });
    }
  }

  _bookmarkFor(sectionId) {
    const aliases = {
      decision: ["decision", "presentation"],
      site: ["site", "parcel"],
      market: ["market", "competitors", "evidence"],
      risk: ["risk", "risks"],
    };
    for (const name of aliases[sectionId] || [sectionId]) {
      const bookmark = this.director.getBookmark(name);
      if (bookmark) return bookmark;
    }
    return null;
  }

  _observeSections() {
    if (typeof globalThis.IntersectionObserver !== "function") return;
    this.sectionObserver = new IntersectionObserver((entries) => {
      const visible = entries
        .filter((entry) => entry.isIntersecting)
        .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
      if (!visible || this.presentation) return;
      const id = visible.target.dataset.reportSection;
      if (id !== this.activeSection) this._focusSection(id, { moveDocument: false });
    }, { rootMargin: "-96px 0px -75% 0px", threshold: [0, 0.1, 0.4] });
    this.root.querySelectorAll("[data-report-section]").forEach((section) => this.sectionObserver.observe(section));
  }

  _setActiveNav(id) {
    this.elements.reportNav.querySelectorAll("[data-section-target]").forEach((button) => {
      button.setAttribute("aria-current", String(button.dataset.sectionTarget === id));
    });
  }

  async _runCommand() {
    const command = this.elements.reportCommandInput.value.trim();
    if (!command) return;
    const result = await this.director.command(command, { project: this.project, location: this.project.location });
    this.elements.reportCommandInput.value = "";
    this._announce(result?.unknown ? "未识别该报告场景命令" : "场景命令已执行");
  }

  enterPresentation() {
    if (this.presentation) return;
    this.presentation = true;
    document.body.classList.add("presentation-mode");
    this.elements.presentationOverlay.setAttribute("aria-hidden", "false");
    const activeIndex = Math.max(0, SECTIONS.findIndex((section) => section.id === this.activeSection));
    this.showPresentationSlide(activeIndex);
  }

  exitPresentation() {
    this.presentation = false;
    document.body.classList.remove("presentation-mode");
    this.elements.presentationOverlay.setAttribute("aria-hidden", "true");
    this.scene.viewer?.resize?.();
    this.root.querySelector(`[data-section-target="${this.activeSection}"]`)?.focus();
  }

  showPresentationSlide(index) {
    if (!this.presentation) return;
    const total = SECTIONS.length;
    this.presentationIndex = (Number(index) + total) % total;
    const section = SECTIONS[this.presentationIndex];
    const content = this.root.querySelector(`[data-section-content="${section.id}"]`);
    const lead = content?.querySelector(".report-lead")?.textContent || content?.querySelector(".finding-list li")?.textContent || section.subtitle;
    this.elements.presentationIndex.textContent = `${String(this.presentationIndex + 1).padStart(2, "0")} / ${String(total).padStart(2, "0")}`;
    this.elements.presentationTitle.textContent = section.title;
    this.elements.presentationText.textContent = lead;
    this._focusSection(section.id, { moveDocument: false });
  }

  async exportReport(format) {
    const existing = format === "html"
      ? this.project?.report?.export_url || this.report?.export?.interactive_html_url || this.report?.export?.html_url
      : this.project?.report?.json_url || this.report?.export?.full_json_url || this.report?.export?.json_url;
    if (safeUrl(existing)) {
      this._openArtifact(existing);
      return;
    }
    try {
      const payload = await this.api.exportProject(this.projectId, format, this.project?.report?.revision);
      const url = format === "html" ? payload.html_url || payload.export_url : payload.json_url;
      if (!safeUrl(url)) throw new Error(`${format.toUpperCase()} 导出尚未生成`);
      this._openArtifact(url);
    } catch (error) {
      this._announce(error.message || "导出失败");
    }
  }

  _openArtifact(url) {
    const anchor = document.createElement("a");
    anchor.href = safeUrl(url);
    anchor.target = "_blank";
    anchor.rel = "noopener";
    anchor.click();
  }

  _showFatal(message) {
    this.root.dataset.state = "error";
    this.elements.reportContent?.setAttribute("aria-busy", "false");
    this.root.querySelectorAll("[data-section-content]").forEach((container) => {
      container.replaceChildren(createElement("p", "report-empty", `报告暂不可用：${message}`));
    });
    if (this.elements.reportDecisionSummary) this.elements.reportDecisionSummary.textContent = "报告暂不可用";
    this._announce(message);
  }

  _announce(message) {
    if (!this.elements.reportLiveRegion) return;
    this.elements.reportLiveRegion.textContent = "";
    requestAnimationFrame(() => { this.elements.reportLiveRegion.textContent = String(message || ""); });
  }

  destroy() {
    if (this.destroyed) return;
    this.destroyed = true;
    clearInterval(this.narrativeTimer);
    this.unsubscribeScene?.();
    this.sectionObserver?.disconnect();
    this.bound.forEach(([target, type, listener, options]) => target?.removeEventListener(type, listener, options));
    this.bound = [];
    this.director.destroy();
  }
}
