const LAYER_COLORS = Object.freeze({
  parcel: "#c87961",
  competitors: "#78acaf",
  resources: "#91aa96",
  risks: "#c86d60",
});

function asArray(value) {
  if (Array.isArray(value)) return value;
  if (value && typeof value === "object") return Object.values(value);
  return [];
}

function firstArray(...values) {
  for (const value of values) {
    const array = asArray(value);
    if (array.length) return array;
  }
  return [];
}

function resourceItems(value, category = "") {
  if (Array.isArray(value)) return value.flatMap((item) => resourceItems(item, category));
  if (!value || typeof value !== "object") return [];
  if (Array.isArray(value.items)) {
    return value.items.map((item) => ({ category: item.category || value.category || category, ...item }));
  }
  if (pointOf(value)) return [{ category: value.category || category, ...value }];
  return Object.entries(value).flatMap(([key, item]) => resourceItems(item, key));
}

function pointOf(item) {
  const source = item?.wgs84 || item?.location?.wgs84 || item?.location || item?.position || item?.coordinate || item?.coordinates || item;
  if (Array.isArray(source) && source.length >= 2) {
    const lng = Number(source[0]);
    const lat = Number(source[1]);
    return Number.isFinite(lng) && Number.isFinite(lat) ? { lng, lat } : null;
  }
  const lng = Number(source?.lng ?? source?.lon ?? source?.longitude);
  const lat = Number(source?.lat ?? source?.latitude);
  return Number.isFinite(lng) && Number.isFinite(lat) ? { lng, lat } : null;
}


function amapPointOf(item) {
  const source = item?.gcj02 || item?.location?.gcj02;
  if (!source) return null;
  const lng = Number(source?.lng ?? source?.lon ?? source?.longitude);
  const lat = Number(source?.lat ?? source?.latitude);
  return Number.isFinite(lng) && Number.isFinite(lat) ? { lng, lat } : null;
}
function safeId(prefix, item, index) {
  const explicit = item?.id || item?.code;
  if (explicit != null && explicit !== "") return String(explicit);
  return `${prefix}-${index + 1}`;
}

function labelOf(item, fallback) {
  return String(item?.display_name || item?.project_name || item?.name || item?.title || item?.label || fallback || "证据").trim();
}

function parcelGeometry(location, report) {
  const geometry = report?.parcel?.geometry || report?.site?.parcel?.geometry || location?.geometry || location?.boundary;
  const coordinates = geometry?.type === "Polygon" ? geometry.coordinates?.[0] : geometry?.coordinates || geometry;
  if (!Array.isArray(coordinates) || !coordinates.length || !Array.isArray(coordinates[0])) return null;
  return coordinates.map((coordinate) => [Number(coordinate[0]), Number(coordinate[1])]).filter(([lng, lat]) => Number.isFinite(lng) && Number.isFinite(lat));
}

export class EvidenceLayers {
  constructor(sceneDirector, { searchRadius = 3000 } = {}) {
    this.sceneDirector = sceneDirector;
    this.searchRadius = searchRadius;
    this.descriptors = [];
    this.idsByLayer = new Map();
  }

  build(payload = {}, { location: explicitLocation } = {}) {
    const project = payload.project || payload.data?.project || {};
    const report = payload.report || payload.data?.report || project.report_data || payload.result || payload;
    const location = explicitLocation || project.location || report.location || report.parcel?.location;
    const sitePoint = pointOf(location);
    const siteAmapPoint = amapPointOf(location);
    const descriptors = [];

    if (sitePoint) {
      const boundary = parcelGeometry(location, report);
      if (boundary?.length >= 3) {
        descriptors.push({
          id: "parcel-boundary",
          layer: "parcel",
          type: "polygon",
          position: sitePoint,
          amapPosition: siteAmapPoint || sitePoint,
          coordinates: boundary,
          label: labelOf(location, "目标地块"),
          color: LAYER_COLORS.parcel,
          source: location?.source || "project",
        });
      } else {
        descriptors.push({
          id: "parcel-search-radius",
          layer: "searchArea",
          type: "circle",
          position: sitePoint,
          amapPosition: siteAmapPoint || sitePoint,
          radius: Number(report?.site?.search_radius || this.searchRadius),
          label: "3 km 调研范围（非地块边界）",
          color: LAYER_COLORS.parcel,
          source: location?.source || "project",
        });
      }
      descriptors.push({
        id: "parcel-anchor",
        layer: "parcel",
        type: "point",
        position: sitePoint,
        amapPosition: siteAmapPoint || sitePoint,
        label: labelOf(location, "目标地块"),
        color: LAYER_COLORS.parcel,
        size: 12,
        source: location?.source || "project",
      });
    }

    const competitors = firstArray(
      report?.market?.competitors,
      report?.competitors,
      report?.evidence?.competitors,
      payload?.evidence?.competitors,
    );
    competitors.forEach((item, index) => {
      const position = pointOf(item);
      if (!position) return;
      descriptors.push({
        id: safeId("competitor", item, index),
        layer: "competitors",
        type: "point",
        position,
        amapPosition: amapPointOf(item) || position,
        label: labelOf(item, `竞品 ${index + 1}`),
        color: LAYER_COLORS.competitors,
        size: 9,
        source: item.source || "market",
        data: item,
      });
    });

    const resources = resourceItems(firstArray(
      report?.site?.resources,
      report?.site?.pois,
      report?.amenities,
      report?.resources,
      report?.evidence?.resources,
      payload?.evidence?.resources,
    ));
    resources.forEach((item, index) => {
      const position = pointOf(item);
      if (!position) return;
      descriptors.push({
        id: safeId("resource", item, index),
        layer: "resources",
        type: "point",
        position,
        amapPosition: amapPointOf(item) || position,
        label: labelOf(item, `资源 ${index + 1}`),
        color: LAYER_COLORS.resources,
        size: 8,
        source: item.source || item.category || "site",
        data: item,
      });
    });

    const risks = firstArray(
      report?.risk?.items,
      report?.risks,
      report?.hard_stops,
      report?.evidence_gaps,
      project?.evidence_gaps,
      payload?.evidence?.risks,
    );
    risks.forEach((item, index) => {
      const position = pointOf(item) || sitePoint;
      if (!position) return;
      descriptors.push({
        id: safeId("risk", item, index),
        layer: "risks",
        type: "point",
        position,
        amapPosition: amapPointOf(item) || position,
        label: labelOf(item, item?.field || `风险 ${index + 1}`),
        color: LAYER_COLORS.risks,
        size: 10,
        source: item.source || "risk",
        data: item,
      });
    });

    const seen = new Set();
    this.descriptors = descriptors.filter((descriptor) => {
      if (seen.has(descriptor.id)) return false;
      seen.add(descriptor.id);
      return true;
    });
    this.idsByLayer.clear();
    this.descriptors.forEach((descriptor) => {
      const ids = this.idsByLayer.get(descriptor.layer) || [];
      ids.push(descriptor.id);
      this.idsByLayer.set(descriptor.layer, ids);
    });
    return this.descriptors;
  }

  sync(payload, options) {
    const descriptors = this.build(payload, options);
    this.sceneDirector.setEvidence(descriptors);
    return descriptors;
  }

  setVisible(layer, visible) {
    return this.sceneDirector.setLayerVisibility(layer, visible);
  }

  focus(layer) {
    const ids = this.idsByLayer.get(layer) || [];
    this.sceneDirector.execute({ type: "highlight", entity_ids: ids });
    return ids;
  }

  clearHighlight() {
    this.sceneDirector.execute({ type: "highlight", entity_ids: [] });
  }

  getIds(layer) {
    return [...(this.idsByLayer.get(layer) || [])];
  }
}
