export const MAP_TOPICS = Object.freeze([
  Object.freeze({ id: "overview", label: "综合", mode: "2d", layers: ["parcel", "competitors"] }),
  Object.freeze({ id: "site", label: "区位边界", mode: "2d", layers: ["parcel", "searchArea"] }),
  Object.freeze({ id: "access", label: "周边配套", mode: "2d", layers: ["parcel", "searchArea", "resources"] }),
  Object.freeze({ id: "live", label: "高德实况", mode: "2d", layers: ["parcel", "resources"] }),
  Object.freeze({ id: "price", label: "竞品价格", mode: "2d", layers: ["parcel", "competitors", "heat"] }),
  Object.freeze({ id: "supply", label: "供给去化", mode: "2d", layers: ["parcel", "competitors"] }),
  Object.freeze({ id: "building", label: "建筑类型", mode: "3d", layers: ["parcel", "competitors", "buildings"] }),
  Object.freeze({ id: "risk", label: "风险", mode: "2d", layers: ["parcel", "risks"] }),
  Object.freeze({ id: "evidence", label: "证据", mode: "2d", layers: ["parcel"] }),
]);

export class LayerRegistry {
  constructor(evidenceLayers) {
    this.evidenceLayers = evidenceLayers;
    this.known = new Set([
      "parcel",
      "searchArea",
      "competitors",
      "resources",
      "risks",
      "heat",
      "links",
      "lines",
      "pulseLines",
      "buildings",
    ]);
  }

  apply(ids = []) {
    const visible = new Set(ids);
    this.known.forEach((id) => this.evidenceLayers.setVisible(id, visible.has(id)));
    return [...visible];
  }

  topic(id) {
    return MAP_TOPICS.find((item) => item.id === id) || MAP_TOPICS[0];
  }
}
