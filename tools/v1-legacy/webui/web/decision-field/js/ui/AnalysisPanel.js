const SECTION_LABELS = Object.freeze({
  decision: "决策总览",
  site: "场地区位",
  market: "市场样本",
  customer: "客群机会",
  product: "产品配比",
  design: "设计约束",
  finance: "财务边界",
  risk: "风险与缺口",
  provenance: "证据审计",
});

const TOPIC_LABELS = Object.freeze({
  overview: "空间总览",
  site: "区位与边界",
  access: "周边配套",
  live: "高德实时空间",
  price: "竞品价格",
  supply: "供给与去化",
  building: "建筑类型",
  risk: "空间风险",
  evidence: "证据审计",
});

const FIELD_LABELS = Object.freeze({
  unit_price_cny: "均价",
  unitPriceCny: "均价",
  price: "价格",
  distance_km: "距地块",
  distanceKm: "距地块",
  district: "行政区",
  sub_district: "板块",
  property_type: "物业类型",
  building_type: "建筑类型",
  sale_status: "销售状态",
  status: "状态",
  floor_area_ratio: "容积率",
  plot_ratio: "容积率",
  total_units: "供应套数",
  opening_date: "取证/开盘",
  evidenceGrade: "证据等级",
  confidence: "置信度",
  source: "来源",
});

const PREFERRED_FIELDS = Object.freeze([
  "unit_price_cny",
  "distance_km",
  "district",
  "sub_district",
  "property_type",
  "building_type",
  "sale_status",
  "floor_area_ratio",
  "total_units",
  "opening_date",
]);

const BUILDING_BUCKETS = Object.freeze({
  highrise: { label: "高层住宅", color: "#79a7c5" },
  midrise: { label: "小高层", color: "#8fb594" },
  lowrise: { label: "低密住区", color: "#d5ad68" },
  commercial: { label: "商业办公", color: "#c6819d" },
  mixed: { label: "其他/待核", color: "#8e9892" },
});

function buildingBucket(value) {
  const text = Array.isArray(value) ? value.join("、") : String(value || "");
  if (/办公|写字楼|商业|商办|公寓/.test(text)) return "commercial";
  if (/洋房|别墅|叠拼|低层/.test(text)) return "lowrise";
  if (/小高层|多层/.test(text)) return "midrise";
  if (/高层|超高层/.test(text)) return "highrise";
  return "mixed";
}

function formatValue(value, key = "") {
  if (value == null || value === "") return "待补";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "number") {
    const formatted = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(value);
    if (/unit_price|price_cny/i.test(key)) return `¥${formatted}/㎡`;
    if (/distance_km/i.test(key)) return `${formatted} km`;
    if (/confidence|ratio|rate/i.test(key) && value >= 0 && value <= 1) return `${Math.round(value * 100)}%`;
    return formatted;
  }
  if (Array.isArray(value)) return value.map((item) => String(item)).join("、") || "待补";
  if (typeof value === "object") return Object.values(value).filter((item) => typeof item !== "object").join("、") || "已记录";
  return String(value).replace(/^\{|\}$/g, "").replaceAll(",", "、");
}

function scalarEntries(value, limit = 10) {
  if (!value || typeof value !== "object") return [];
  const entries = Object.entries(value).filter(([, item]) => item != null && item !== "" && typeof item !== "function");
  const preferred = PREFERRED_FIELDS.flatMap((key) => entries.filter(([candidate]) => candidate === key));
  const remaining = entries.filter(([key]) => !PREFERRED_FIELDS.includes(key) && ["string", "number", "boolean"].includes(typeof value[key]));
  return [...preferred, ...remaining].slice(0, limit);
}

function featureList(manifest) {
  return manifest.layers?.flatMap((layer) => layer.features || []) || [];
}

function layerFeatures(manifest, id) {
  return manifest.layers?.find((layer) => layer.id === id)?.features || [];
}

function create(tag, className = "", text = null) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text != null) element.textContent = String(text);
  return element;
}

export class AnalysisPanel {
  constructor(root, { onClose = null, onSelect = null, onPoiSelect = null, onPoiRoute = null } = {}) {
    this.root = root;
    this.onClose = onClose;
    this.onSelect = onSelect;
    this.onPoiSelect = onPoiSelect;
    this.onPoiRoute = onPoiRoute;
    this.poiState = { category: null, label: "周边", status: "idle", items: [], error: "" };
    this.amapContext = null;
    this.root?.addEventListener("click", (event) => {
      const action = event.target.closest?.("[data-panel-action]")?.dataset.panelAction;
      if (action === "close") this.onClose?.();
      const featureId = event.target.closest?.("[data-feature-id]")?.dataset.featureId;
      if (featureId) this.onSelect?.(featureId);
      const poiId = event.target.closest?.("[data-poi-id]")?.dataset.poiId;
      if (poiId) this.onPoiSelect?.(poiId);
      const routeButton = event.target.closest?.("[data-route-poi-id]");
      if (routeButton) this.onPoiRoute?.(routeButton.dataset.routePoiId, routeButton.dataset.routeMode || "walking");
    });
  }

  setPoiResults(payload = {}) {
    this.poiState = { ...this.poiState, ...payload, items: Array.isArray(payload.items) ? payload.items : [] };
  }

  setAmapContext(payload) {
    this.amapContext = payload;
  }

  render(manifest, sectionId, selectedId = null, { topic = "overview" } = {}) {
    if (!this.root || !manifest) return;
    const selected = featureList(manifest).find((item) => String(item.id) === String(selectedId));
    const title = topic !== "overview" ? TOPIC_LABELS[topic] : SECTION_LABELS[sectionId] || "空间研判";
    this.root.querySelector("[data-panel-title]").textContent = title;
    const status = this.root.querySelector("[data-panel-status]");
    status.textContent = selected?.scenarioStatus === "simulated" ? "方案推演" : selected?.scenarioStatus === "inferred" ? "推断" : "观测数据";
    status.dataset.status = selected?.scenarioStatus || "observed";

    const body = this.root.querySelector("[data-panel-body]");
    body.replaceChildren();
    this._renderCounts(body, manifest);
    if (selected) this._renderSelected(body, selected);
    this._renderMetrics(body, selected?.metrics || manifest.metrics?.[sectionId] || {});

    if (topic === "access") this._renderPoi(body);
    else if (topic === "live") this._renderAmapContext(body);
    else if (topic === "risk" || sectionId === "risk") this._renderObjectList(body, layerFeatures(manifest, "risks"), "risk");
    else if (["overview", "price", "supply"].includes(topic) || sectionId === "market") {
      this._renderObjectList(body, layerFeatures(manifest, "competitors"), "competitor");
    }

    if (topic === "building") {
      const competitors = layerFeatures(manifest, "competitors");
      this._renderBuildingSummary(body, competitors);
      this._renderObjectList(body, competitors, "building");
      body.append(create("p", "analysis-note", "建筑颜色按最近竞品的产品类型映射至样本影响区，用于板块识别，不代表建筑单体实测边界。"));
    }
    if (!body.childElementCount) body.append(create("div", "analysis-empty", "当前专题暂无可定位数据。切换其他专题或补充项目证据后再分析。"));
  }

  _renderCounts(body, manifest) {
    const counts = [
      ["竞品", layerFeatures(manifest, "competitors").length],
      ["POI", this.poiState.items.length || layerFeatures(manifest, "resources").length],
      ["风险", layerFeatures(manifest, "risks").length],
    ];
    const summary = create("div", "analysis-counts");
    counts.forEach(([label, value]) => {
      const item = create("div");
      item.append(create("strong", "", value), create("span", "", label));
      summary.append(item);
    });
    body.append(summary);
  }

  _renderAmapContext(body) {
    const context = this.amapContext;
    if (!context) {
      body.append(create("div", "analysis-loading", "正在从高德获取地址、行政区、天气与实时路况…"));
      return;
    }
    const heading = create("div", "analysis-detail");
    heading.append(
      create("span", "", "高德官方空间事实"),
      create("strong", "", context.formattedAddress || "地址待核"),
      create("small", "", [context.province, context.city, context.district, context.township, context.adcode].filter(Boolean).join(" · ")),
    );
    body.append(heading);
    const metrics = create("dl", "analysis-metric-grid");
    const weather = context.weather || {};
    [
      ["实时天气", weather.condition || "暂无"],
      ["气温", weather.temperature == null ? "暂无" : `${weather.temperature} ℃`],
      ["湿度", weather.humidity == null ? "暂无" : `${weather.humidity}%`],
      ["风况", weather.windDirection ? `${weather.windDirection}风 ${weather.windPower || "-"}级` : "暂无"],
      ["行政边界", context.boundaryCount ? `${context.boundaryCount} 组` : "暂无"],
      ["实时路况", context.traffic ? "已叠加" : "不可用"],
    ].forEach(([label, value]) => {
      const item = create("div");
      item.append(create("dt", "", label), create("dd", "", value));
      metrics.append(item);
    });
    body.append(metrics, create("p", "analysis-note", `数据源：${context.source} · 更新时间：${weather.reportTime || "实时查询"}`));
  }

  _renderSelected(body, selected) {
    const heading = create("div", "analysis-detail");
    heading.append(
      create("span", "", selected.category || "地图对象"),
      create("strong", "", selected.label || selected.id),
      create("small", "", [selected.source, selected.evidenceGrade, selected.observedAt].filter(Boolean).join(" · ") || "来源待核验"),
    );
    body.append(heading);
  }

  _renderMetrics(body, source) {
    const entries = scalarEntries(source);
    if (!entries.length) return;
    const grid = create("dl", "analysis-metric-grid");
    entries.forEach(([key, value]) => {
      const item = create("div");
      item.append(create("dt", "", FIELD_LABELS[key] || key.replaceAll("_", " ")), create("dd", "", formatValue(value, key)));
      grid.append(item);
    });
    body.append(grid);
  }

  _renderObjectList(body, items, type) {
    if (!Array.isArray(items) || !items.length) return;
    const heading = create("div", "analysis-list-heading");
    const headingLabel = type === "risk" ? "风险清单" : type === "building" ? "竞品建筑样本" : "相关样本";
    heading.append(create("strong", "", headingLabel), create("span", "", `${items.length} 条`));
    const list = create("div", "analysis-object-list");
    items.slice(0, 12).forEach((item, index) => {
      const button = create("button");
      button.type = "button";
      button.dataset.featureId = item.id;
      const label = create("span", "", item.label || item.id);
      const price = item.metrics?.unit_price_cny;
      const meta = type === "risk"
        ? item.severity || item.evidenceGrade || item.source || "待核"
        : type === "building"
          ? formatValue(item.metrics?.building_type || item.metrics?.property_type || "待核", "building_type")
        : price ? formatValue(Number(price), "unit_price_cny") : item.metrics?.district || item.source || `#${index + 1}`;
      button.append(label, create("small", "", meta));
      list.append(button);
    });
    body.append(heading, list);
  }

  _renderBuildingSummary(body, items) {
    const counts = Object.fromEntries(Object.keys(BUILDING_BUCKETS).map((key) => [key, 0]));
    items.forEach((item) => { counts[buildingBucket(item.metrics?.building_type || item.metrics?.property_type)] += 1; });
    const summary = create("div", "analysis-building-summary");
    Object.entries(BUILDING_BUCKETS).forEach(([key, definition]) => {
      if (!counts[key] && key === "mixed") return;
      const item = create("div");
      const swatch = create("i");
      swatch.style.setProperty("--building-color", definition.color);
      item.append(swatch, create("strong", "", counts[key]), create("span", "", definition.label));
      summary.append(item);
    });
    body.append(summary);
  }

  _renderPoi(body) {
    const heading = create("div", "analysis-list-heading");
    heading.append(create("strong", "", `${this.poiState.label || "周边"}设施`), create("span", "", `${this.poiState.items.length} 条`));
    body.append(heading);
    if (this.poiState.status === "loading") {
      body.append(create("div", "analysis-loading", "正在从高德检索地块周边设施…"));
      return;
    }
    if (!this.poiState.items.length) {
      body.append(create("div", "analysis-empty", this.poiState.error || "当前类别没有返回可定位设施，可切换其他 POI 类别。"));
      return;
    }
    const list = create("div", "analysis-object-list analysis-object-list--poi");
    this.poiState.items.slice(0, 15).forEach((item) => {
      const row = create("div", "analysis-poi-row");
      const button = create("button");
      button.type = "button";
      button.dataset.poiId = item.id;
      button.append(
        create("span", "", item.name),
        create("small", "", item.distance == null ? "距离待核" : `${Math.round(item.distance)} m`),
      );
      const actions = create("div", "analysis-poi-actions");
      [["walking", "步行"], ["driving", "驾车"]].forEach(([mode, label]) => {
        const route = create("button", "", label);
        route.type = "button";
        route.dataset.routePoiId = item.id;
        route.dataset.routeMode = mode;
        route.setAttribute("aria-label", `${label}前往${item.name}`);
        actions.append(route);
      });
      row.append(button, actions);
      list.append(row);
    });
    body.append(list);
  }
}
